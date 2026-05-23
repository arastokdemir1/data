#!/usr/bin/env python3
"""
Fetch OPET fuel prices and write CarLog-compatible JSON files.

Outputs:
- fuel_prices.json: compact schema currently consumed by the iOS app.
- fuel_prices_tr_v1.json: richer city/district dataset for future app versions.

Notes:
- OPET exposes gasoline and diesel prices through its public web component API.
- LPG is not present in the current OPET API response, so this script
  tries hasanadiguzel.com for LPG first, then preserves the previous value.
- hasanadiguzel.com is also used as a full fallback when OPET is unavailable.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OPET_API_BASE = "https://api.opet.com.tr/api"
HASANADIGUZEL_API = "http://hasanadiguzel.com.tr/api/akaryakit/sehir={sehir}"
HASANADIGUZEL_SEHIR = "ISTANBUL"  # ASCII — API şehir listesinde hem İSTANBUL hem ISTANBUL var

DEFAULT_LPG_PRICE = 32.30
DEFAULT_PROVINCE_CODES = (34, 934)  # Istanbul Anadolu + Istanbul Avrupa

# hasanadiguzel key adları (gerçek API response'dan)
# data = {"district_id": {"Motorin(Eurodiesel)_TL/lt": "67,02", "Otogaz_TL/lt": "", ...}}
MOTORIN_KEYS = ["Motorin(Eurodiesel)_TL/lt", "Motorin_TL/lt"]
LPG_KEYS = ["Otogaz_TL/lt", "LPG_TL/lt"]
# Not: "Kursunsuz_95(Excellium95)_TL/lt" = premium marka, standart 95 fiyatı yok → benzin için API kullanılmaz


@dataclass(frozen=True)
class CompactPrices:
    gasoline: float
    diesel: float
    lpg: float
    last_update: str


def fetch_json(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "CarLogDataUpdater/1.0 (+https://github.com/arastokdemir1/data)",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"{url} returned HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def fetch_hasanadiguzel(sehir: str = HASANADIGUZEL_SEHIR) -> dict[str, float | None]:
    """
    hasanadiguzel.com il bazlı akaryakıt API'sinden fiyat çeker.
    Ücretsiz, kayıt gerektirmez. OPET'ten farklı olarak LPG içerebilir.
    """
    # Önce ASCII dene, 400 alırsa UTF-8 encode ile dene
    url = HASANADIGUZEL_API.format(sehir=sehir)

    try:
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "CarLogDataUpdater/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = json.loads(r.read().decode("utf-8"))

        if raw.get("code") == 400 or "error" in raw:
            print(f"hasanadiguzel API error: {raw.get('error', raw)}")
            return {"gasoline": None, "diesel": None, "lpg": None}

        # data bir dict: {"district_id": {"Motorin(Eurodiesel)_TL/lt": "67,02", ...}}
        data_section = raw.get("data", {})
        if not data_section or not isinstance(data_section, dict):
            print("hasanadiguzel: unexpected data format")
            return {"gasoline": None, "diesel": None, "lpg": None}

        # Her district'in fiyat dict'ini düzleştir
        all_rows: list[dict] = list(data_section.values()) if isinstance(data_section, dict) else data_section

        def extract(keys: list[str]) -> float | None:
            vals = []
            for row in all_rows:
                if not isinstance(row, dict):
                    continue
                for k in keys:
                    v = row.get(k)
                    if v and str(v).strip() not in ("", "-"):
                        try:
                            fv = float(str(v).replace(",", "."))
                            if fv > 0:
                                vals.append(fv)
                        except (ValueError, TypeError):
                            pass
                        break
            return round(statistics.fmean(vals), 2) if vals else None

        result: dict[str, float | None] = {
            "gasoline": None,  # Excellium premium, standart 95 yok — OPET kullan
            "diesel": extract(MOTORIN_KEYS),
            "lpg": extract(LPG_KEYS),
        }
        print(
            f"hasanadiguzel ({sehir}): "
            f"gasoline={result['gasoline']}, diesel={result['diesel']}, lpg={result['lpg']}"
        )
        return result

    except Exception as e:
        print(f"hasanadiguzel fetch failed: {e}")
        return {"gasoline": None, "diesel": None, "lpg": None}


def parse_opet_date(value: str) -> str:
    parsed = datetime.strptime(value, "%d.%m.%Y").replace(tzinfo=timezone.utc)
    return parsed.isoformat().replace("+00:00", "Z")


def load_previous_lpg(path: Path) -> float:
    if not path.exists():
        return float(os.getenv("DEFAULT_LPG_PRICE", DEFAULT_LPG_PRICE))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        value = payload.get("lpg")
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    except (OSError, json.JSONDecodeError):
        pass
    return float(os.getenv("DEFAULT_LPG_PRICE", DEFAULT_LPG_PRICE))


def normalize_district(row: dict[str, Any]) -> dict[str, Any]:
    prices = row.get("prices") or []
    products: dict[str, dict[str, Any]] = {}

    for item in prices:
        code = item.get("productCode")
        if not code:
            continue
        products[str(code)] = {
            "name": item.get("productName"),
            "shortName": item.get("productShortName"),
            "amount": item.get("amount"),
        }

    gasoline = products.get("A100", {}).get("amount")
    diesel = products.get("A121", {}).get("amount") or products.get("A128", {}).get("amount")

    return {
        "provinceCode": row.get("provinceCode"),
        "provinceName": row.get("provinceName"),
        "districtCode": row.get("districtCode"),
        "districtName": row.get("districtName"),
        "gasoline": gasoline,
        "diesel": diesel,
        "products": products,
    }


def average(values: list[float]) -> float:
    if not values:
        raise RuntimeError("No price values found for compact fuel price output")
    return round(statistics.fmean(values), 2)


def build_compact_prices(
    districts: list[dict[str, Any]],
    province_codes: tuple[int, ...],
    lpg: float,
    last_update: str,
) -> CompactPrices:
    selected = [item for item in districts if item.get("provinceCode") in province_codes]
    if not selected:
        selected = districts

    gasoline_values = [
        float(item["gasoline"])
        for item in selected
        if isinstance(item.get("gasoline"), (int, float)) and item["gasoline"] > 0
    ]
    diesel_values = [
        float(item["diesel"])
        for item in selected
        if isinstance(item.get("diesel"), (int, float)) and item["diesel"] > 0
    ]

    return CompactPrices(
        gasoline=average(gasoline_values),
        diesel=average(diesel_values),
        lpg=round(lpg, 2),
        last_update=last_update,
    )


def parse_province_codes(raw: str | None) -> tuple[int, ...]:
    if not raw:
        return DEFAULT_PROVINCE_CODES
    values = [int(part.strip()) for part in raw.split(",") if part.strip()]
    return tuple(values) or DEFAULT_PROVINCE_CODES


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=".", help="Directory where JSON files are written")
    parser.add_argument(
        "--compact-province-codes",
        default=os.getenv("COMPACT_PROVINCE_CODES"),
        help="Comma-separated OPET province codes for fuel_prices.json averaging",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # hasanadiguzel her zaman dene — LPG için + OPET fallback için
    hadiguzel = fetch_hasanadiguzel()

    # OPET ana kaynak
    opet_ok = False
    districts: list[dict[str, Any]] = []
    last_update = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    last_update_raw = last_update[:10]
    compact: CompactPrices | None = None

    try:
        last_update_payload = fetch_json(f"{OPET_API_BASE}/fuelprices/lastupdate")
        all_prices_payload = fetch_json(f"{OPET_API_BASE}/fuelprices/allprices")

        if not isinstance(all_prices_payload, list):
            raise RuntimeError("OPET allprices response is not a list")

        last_update_raw = last_update_payload.get("lastUpdateDate")
        if not isinstance(last_update_raw, str):
            raise RuntimeError("OPET lastupdate response has no lastUpdateDate")

        last_update = parse_opet_date(last_update_raw)
        districts = [normalize_district(row) for row in all_prices_payload if isinstance(row, dict)]

        # LPG: hasanadiguzel'den al, yoksa önceki değeri koru
        lpg = hadiguzel.get("lpg") or load_previous_lpg(output_dir / "fuel_prices.json")

        compact = build_compact_prices(
            districts=districts,
            province_codes=parse_province_codes(args.compact_province_codes),
            lpg=lpg,
            last_update=last_update,
        )
        opet_ok = True
        print(f"OPET OK: gasoline={compact.gasoline}, diesel={compact.diesel}, lpg={compact.lpg}")

    except Exception as e:
        print(f"OPET fetch failed: {e}")

    # OPET başarısızsa hasanadiguzel ile devam et
    if not opet_ok:
        print("Falling back to hasanadiguzel.com for all prices...")
        g = hadiguzel.get("gasoline")
        d = hadiguzel.get("diesel")
        lpg_val = hadiguzel.get("lpg") or load_previous_lpg(output_dir / "fuel_prices.json")

        if g is None or d is None:
            print("ERROR: Both OPET and hasanadiguzel failed. Keeping existing files.")
            return 1

        compact = CompactPrices(gasoline=g, diesel=d, lpg=lpg_val, last_update=last_update)

    assert compact is not None

    sources = ["OPET public fuel price API"] if opet_ok else ["hasanadiguzel.com (fallback)"]
    if hadiguzel.get("lpg") is not None:
        sources.append("hasanadiguzel.com (LPG)")

    compact_payload = {
        "gasoline": compact.gasoline,
        "diesel": compact.diesel,
        "lpg": compact.lpg,
        "lastUpdate": compact.last_update,
    }
    rich_payload = {
        "version": f"{'opet' if opet_ok else 'hadiguzel'}-{last_update_raw}",
        "lastUpdated": compact.last_update,
        "currency": "TRY",
        "source": ", ".join(sources),
        "sourceUrl": "https://www.opet.com.tr/akaryakit-fiyatlari",
        "confidence": "provider_recommended_price",
        "notes": [
            "OPET publishes recommended fuel prices; station pump prices may differ.",
            "LPG sourced from hasanadiguzel.com when available, otherwise preserved from previous run.",
            "hasanadiguzel.com used as full fallback when OPET API is unavailable.",
        ],
        "districtPrices": districts,
    }

    write_json(output_dir / "fuel_prices.json", compact_payload)
    if districts:
        write_json(output_dir / "fuel_prices_tr_v1.json", rich_payload)

    print(
        f"Done: gasoline={compact.gasoline}, diesel={compact.diesel}, "
        f"lpg={compact.lpg}, lastUpdate={compact.last_update}, opet_ok={opet_ok}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
