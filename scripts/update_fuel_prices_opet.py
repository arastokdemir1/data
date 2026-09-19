#!/usr/bin/env python3
"""Fetch, validate and atomically publish CarLog fuel price data.

OPET is the primary source. Aytemiz's official public price table is a real
fallback for gasoline and diesel; Hasan Adigüzel is used only for LPG when it
has a value. No output file is touched before the candidate data is validated.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import statistics
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo


OPET_API_BASE = "https://api.opet.com.tr/api"
AYTEMIZ_GASOLINE_URL = "https://www.aytemiz.com.tr/akaryakit-fiyatlari/benzin-fiyatlari"
HASANADIGUZEL_API = "https://hasanadiguzel.com.tr/api/akaryakit/sehir={sehir}"
HASANADIGUZEL_SEHIR = "ISTANBUL"
DEFAULT_LPG_PRICE = 32.30
DEFAULT_PROVINCE_CODES = (34, 934)
MIN_DISTRICT_ROWS = 70
MIN_DISTRICTS = 70
MIN_PROVINCES = 70
MIN_PRICE = 5.0
MAX_PRICE = 500.0
MAX_SOURCE_AGE = timedelta(days=3)
MAX_PRICE_CHANGE_RATIO = 0.35
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_TIMEOUT_SECONDS = 90
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
MOTORIN_KEYS = ["Motorin(Eurodiesel)_TL/lt", "Motorin_TL/lt"]
LPG_KEYS = ["Otogaz_TL/lt", "LPG_TL/lt"]


class DataValidationError(RuntimeError):
    """The source returned data that must not be auto-published."""


@dataclass(frozen=True)
class CompactPrices:
    gasoline: float
    diesel: float
    lpg: float
    last_update: str


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={
        "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        "User-Agent": "CarLogDataUpdater/2.0 (+https://github.com/arastokdemir1/data)",
    })


def fetch_bytes(
    url: str,
    *,
    attempts: int = DOWNLOAD_ATTEMPTS,
    timeout: int = DOWNLOAD_TIMEOUT_SECONDS,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleeper: Callable[[float], None] = time.sleep,
) -> bytes:
    """Read a response incrementally and retry transient provider failures."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with opener(_request(url), timeout=timeout) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise RuntimeError(f"{url} returned HTTP {status}")
                chunks: list[bytes] = []
                total = 0
                while chunk := response.read(64 * 1024):
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise DataValidationError(f"{url} exceeds {MAX_DOWNLOAD_BYTES} bytes")
                    chunks.append(chunk)
                if not chunks:
                    raise DataValidationError(f"{url} returned an empty response")
                return b"".join(chunks)
        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
            last_error = error
            if attempt + 1 < attempts:
                sleeper(float(2**attempt))
    raise RuntimeError(f"download failed after {attempts} attempts: {url}: {last_error}")


def fetch_json(url: str, **kwargs: Any) -> Any:
    try:
        return json.loads(fetch_bytes(url, **kwargs).decode("utf-8"))
    except json.JSONDecodeError as error:
        raise DataValidationError(f"{url} returned invalid JSON: {error}") from error


def parse_opet_date(value: str) -> str:
    local = datetime.strptime(value, "%d.%m.%Y").replace(tzinfo=ZoneInfo("Europe/Istanbul"))
    return local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_price(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    try:
        number = float(str(value).strip().replace(".", "").replace(",", "."))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def normalized_label(value: Any) -> str:
    return "".join(
        character for character in unicodedata.normalize("NFD", str(value).casefold())
        if unicodedata.category(character) != "Mn"
    )


def normalize_district(row: dict[str, Any]) -> dict[str, Any]:
    products = {
        str(item.get("productCode")): {
            "name": item.get("productName"), "shortName": item.get("productShortName"), "amount": item.get("amount")
        }
        for item in row.get("prices", [])
        if isinstance(item, dict) and item.get("productCode")
    }
    return {
        "provinceCode": row.get("provinceCode"), "provinceName": row.get("provinceName"),
        "districtCode": row.get("districtCode"), "districtName": row.get("districtName"),
        "gasoline": products.get("A100", {}).get("amount"),
        "diesel": products.get("A121", {}).get("amount") or products.get("A128", {}).get("amount"),
        "products": products,
    }


def _strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html.unescape(value))).strip()


def parse_aytemiz_prices(payload: str) -> tuple[list[dict[str, Any]], str]:
    """Parse Aytemiz's server-rendered official 95-octane price table."""
    date_match = re.search(r"Son Güncelleme:\s*(\d{2}\.\d{2}\.\d{4})(?:\s+(\d{2}:\d{2}))?", payload)
    if not date_match:
        raise DataValidationError("Aytemiz update timestamp is missing")
    local = datetime.strptime(f"{date_match.group(1)} {date_match.group(2) or '00:00'}", "%d.%m.%Y %H:%M")
    last_updated = local.replace(tzinfo=ZoneInfo("Europe/Istanbul")).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    rows: list[dict[str, Any]] = []
    for raw_row in re.findall(r"<tr[^>]*>(.*?)(?=<tr|</tbody>)", payload, flags=re.IGNORECASE | re.DOTALL):
        cells = [_strip_html(cell) for cell in re.findall(
            r"<t[dh]\b[^>]*>(.*?)(?=<(?:t[dh]|tr|/tbody))", raw_row, flags=re.IGNORECASE | re.DOTALL
        )]
        if len(cells) < 3:
            continue
        gasoline, diesel = parse_price(cells[1]), parse_price(cells[2])
        if gasoline is None or diesel is None:
            continue
        rows.append({
            "provinceCode": None, "provinceName": cells[0].split("/")[0].strip(),
            "districtCode": None, "districtName": cells[0], "gasoline": gasoline, "diesel": diesel, "products": {},
        })
    if len(rows) < MIN_PROVINCES:
        raise DataValidationError(f"Aytemiz table has only {len(rows)} usable province rows")
    return rows, last_updated


def fetch_aytemiz_prices(**kwargs: Any) -> tuple[list[dict[str, Any]], str]:
    return parse_aytemiz_prices(fetch_bytes(AYTEMIZ_GASOLINE_URL, **kwargs).decode("utf-8"))


def fetch_hasanadiguzel() -> dict[str, float | None]:
    try:
        raw = fetch_json(HASANADIGUZEL_API.format(sehir=HASANADIGUZEL_SEHIR), attempts=2, timeout=20)
        data = raw.get("data", {}) if isinstance(raw, dict) else {}
        if not isinstance(data, dict):
            return {"diesel": None, "lpg": None}

        def extract(keys: list[str]) -> float | None:
            values = []
            for row in data.values():
                if not isinstance(row, dict):
                    continue
                for key in keys:
                    if (value := parse_price(row.get(key))) is not None:
                        values.append(value)
                        break
            return round(statistics.fmean(values), 2) if values else None

        return {"diesel": extract(MOTORIN_KEYS), "lpg": extract(LPG_KEYS)}
    except Exception as error:
        print(f"Hasan Adigüzel fetch failed: {error}")
        return {"diesel": None, "lpg": None}


def load_previous_lpg(path: Path) -> float:
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("lpg")
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    except (OSError, json.JSONDecodeError):
        pass
    return float(os.getenv("DEFAULT_LPG_PRICE", DEFAULT_LPG_PRICE))


def average(values: list[float]) -> float:
    if not values:
        raise DataValidationError("no usable fuel prices found")
    return round(statistics.fmean(values), 2)


def build_compact_prices(districts: list[dict[str, Any]], lpg: float, last_update: str) -> CompactPrices:
    selected = [item for item in districts if item.get("provinceCode") in DEFAULT_PROVINCE_CODES]
    if not selected:
        selected = [item for item in districts if "istanbul" in normalized_label(item.get("provinceName", ""))]
    if not selected:
        selected = districts
    return CompactPrices(
        gasoline=average([float(item["gasoline"]) for item in selected if isinstance(item.get("gasoline"), (int, float)) and item["gasoline"] > 0]),
        diesel=average([float(item["diesel"]) for item in selected if isinstance(item.get("diesel"), (int, float)) and item["diesel"] > 0]),
        lpg=round(lpg, 2), last_update=last_update,
    )


def validate_candidate(compact: CompactPrices, districts: list[dict[str, Any]], *, previous: dict[str, Any] | None, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    try:
        source_time = datetime.fromisoformat(compact.last_update.replace("Z", "+00:00"))
    except ValueError as error:
        raise DataValidationError(f"invalid source timestamp: {compact.last_update}") from error
    if source_time > now + timedelta(hours=6) or now - source_time > MAX_SOURCE_AGE:
        raise DataValidationError(f"source timestamp is not fresh: {compact.last_update}")
    for name, value in {"gasoline": compact.gasoline, "diesel": compact.diesel, "lpg": compact.lpg}.items():
        if not MIN_PRICE <= value <= MAX_PRICE:
            raise DataValidationError(f"{name} price {value} is outside {MIN_PRICE}…{MAX_PRICE}")
    provinces = {normalized_label(item.get("provinceName", "")) for item in districts if item.get("provinceName")}
    # Some providers use the same literal district label (for example "MERKEZ")
    # in many provinces. Count a geographic district cell, not label text alone.
    district_cells = {
        str(item.get("districtCode")) if item.get("districtCode") else
        f"{normalized_label(item.get('provinceName', ''))}:{normalized_label(item.get('districtName', ''))}"
        for item in districts
        if item.get("districtCode") or item.get("provinceName") or item.get("districtName")
    }
    usable = [item for item in districts if isinstance(item.get("gasoline"), (int, float)) and isinstance(item.get("diesel"), (int, float))]
    if len(usable) < MIN_DISTRICT_ROWS or len(district_cells) < MIN_DISTRICTS or len(provinces) < MIN_PROVINCES:
        raise DataValidationError(
            f"insufficient coverage: {len(usable)} rows / {len(district_cells)} districts / {len(provinces)} provinces"
        )
    for item in usable:
        for field in ("gasoline", "diesel"):
            value = float(item[field])
            if not MIN_PRICE <= value <= MAX_PRICE:
                raise DataValidationError(f"{item.get('provinceName')} {field}={value} is implausible")
    if previous:
        for name, value in {"gasoline": compact.gasoline, "diesel": compact.diesel, "lpg": compact.lpg}.items():
            old = previous.get(name)
            if isinstance(old, (int, float)) and old > 0 and abs(value - old) / old > MAX_PRICE_CHANGE_RATIO:
                raise DataValidationError(f"{name} changed from {old} to {value} by more than {MAX_PRICE_CHANGE_RATIO:.0%}")


def write_json_atomic(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_payloads(compact: CompactPrices, districts: list[dict[str, Any]], *, source: str, source_url: str) -> tuple[dict[str, Any], dict[str, Any]]:
    compact_payload = {"gasoline": compact.gasoline, "diesel": compact.diesel, "lpg": compact.lpg, "lastUpdate": compact.last_update}
    rich_payload = {
        "version": f"fuel-v2-{compact.last_update[:10]}", "lastUpdated": compact.last_update, "currency": "TRY",
        "source": source, "sourceUrl": source_url, "confidence": "provider_recommended_price",
        "notes": [
            "Provider prices are recommended pump prices; individual stations may differ.",
            "LPG is preserved from the last verified value when no provider value is available.",
            "Candidate data is range, coverage, freshness and change-rate validated before publication.",
        ], "districtPrices": districts,
    }
    return compact_payload, rich_payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=".", help="Directory where JSON files are written")
    args = parser.parse_args()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    previous_path = output_dir / "fuel_prices.json"
    try:
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        previous = None
    lpg = fetch_hasanadiguzel().get("lpg") or load_previous_lpg(previous_path)
    try:
        last_update_payload = fetch_json(f"{OPET_API_BASE}/fuelprices/lastupdate")
        source_date = last_update_payload.get("lastUpdateDate") if isinstance(last_update_payload, dict) else None
        if not isinstance(source_date, str):
            raise DataValidationError("OPET lastupdate response has no lastUpdateDate")
        all_prices = fetch_json(f"{OPET_API_BASE}/fuelprices/allprices")
        if not isinstance(all_prices, list):
            raise DataValidationError("OPET allprices response is not a list")
        districts = [normalize_district(row) for row in all_prices if isinstance(row, dict)]
        compact = build_compact_prices(districts, lpg, parse_opet_date(source_date))
        source, source_url = "OPET public fuel price API", "https://www.opet.com.tr/akaryakit-fiyatlari"
    except Exception as opet_error:
        print(f"OPET fetch failed: {opet_error}")
        try:
            districts, last_update = fetch_aytemiz_prices()
            compact = build_compact_prices(districts, lpg, last_update)
            source, source_url = "Aytemiz official public price table (OPET fallback)", AYTEMIZ_GASOLINE_URL
        except Exception as fallback_error:
            raise RuntimeError(f"both price providers failed; no files changed: OPET={opet_error}; Aytemiz={fallback_error}") from fallback_error
    validate_candidate(compact, districts, previous=previous)
    compact_payload, rich_payload = build_payloads(compact, districts, source=source, source_url=source_url)
    write_json_atomic(output_dir / "fuel_prices.json", compact_payload)
    write_json_atomic(output_dir / "fuel_prices_tr_v1.json", rich_payload)
    print(f"Published verified fuel data: {source}; gasoline={compact.gasoline}, diesel={compact.diesel}, lpg={compact.lpg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
