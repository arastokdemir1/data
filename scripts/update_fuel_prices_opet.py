#!/usr/bin/env python3
"""
Fetch OPET fuel prices and write CarLog-compatible JSON files.

Outputs:
- fuel_prices.json: compact schema currently consumed by the iOS app.
- fuel_prices_tr_v1.json: richer city/district dataset for future app versions.

Notes:
- OPET exposes gasoline and diesel prices through its public web component API.
- LPG is not present in the current OPET fuel price response, so this script
  preserves the previous LPG value from fuel_prices.json when available.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OPET_API_BASE = "https://api.opet.com.tr/api"
DEFAULT_LPG_PRICE = 32.30
DEFAULT_PROVINCE_CODES = (34, 934)  # Istanbul Anadolu + Istanbul Avrupa


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

    last_update_payload = fetch_json(f"{OPET_API_BASE}/fuelprices/lastupdate")
    all_prices_payload = fetch_json(f"{OPET_API_BASE}/fuelprices/allprices")

    if not isinstance(all_prices_payload, list):
        raise RuntimeError("OPET allprices response is not a list")

    last_update_raw = last_update_payload.get("lastUpdateDate")
    if not isinstance(last_update_raw, str):
        raise RuntimeError("OPET lastupdate response has no lastUpdateDate")

    last_update = parse_opet_date(last_update_raw)
    districts = [normalize_district(row) for row in all_prices_payload if isinstance(row, dict)]
    lpg = load_previous_lpg(output_dir / "fuel_prices.json")
    compact = build_compact_prices(
        districts=districts,
        province_codes=parse_province_codes(args.compact_province_codes),
        lpg=lpg,
        last_update=last_update,
    )

    compact_payload = {
        "gasoline": compact.gasoline,
        "diesel": compact.diesel,
        "lpg": compact.lpg,
        "lastUpdate": compact.last_update,
    }
    rich_payload = {
        "version": f"opet-{last_update_raw}",
        "lastUpdated": compact.last_update,
        "currency": "TRY",
        "source": "OPET public fuel price API",
        "sourceUrl": "https://www.opet.com.tr/akaryakit-fiyatlari",
        "confidence": "provider_recommended_price",
        "notes": [
            "OPET publishes recommended fuel prices; station pump prices may differ.",
            "LPG is not included in the current OPET API response and is preserved in fuel_prices.json.",
        ],
        "districtPrices": districts,
    }

    write_json(output_dir / "fuel_prices.json", compact_payload)
    write_json(output_dir / "fuel_prices_tr_v1.json", rich_payload)

    print(
        "Updated fuel prices: "
        f"gasoline={compact.gasoline}, diesel={compact.diesel}, "
        f"lpg={compact.lpg}, lastUpdate={compact.last_update}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
