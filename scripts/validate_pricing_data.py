#!/usr/bin/env python3
"""Fail-closed semantic validation for every data file published to CarLog."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from update_fuel_prices_opet import CompactPrices, DataValidationError, validate_candidate


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DataValidationError(f"invalid JSON: {path.name}: {error}") from error


def validate_fuel(root: Path) -> None:
    compact = load_json(root / "fuel_prices.json")
    rich = load_json(root / "fuel_prices_tr_v1.json")
    if not isinstance(compact, dict) or not isinstance(rich, dict):
        raise DataValidationError("fuel payloads must be objects")
    candidate = CompactPrices(
        gasoline=float(compact["gasoline"]), diesel=float(compact["diesel"]),
        lpg=float(compact["lpg"]), last_update=str(compact["lastUpdate"]),
    )
    if rich.get("lastUpdated") != candidate.last_update:
        raise DataValidationError("compact and rich fuel timestamps differ")
    districts = rich.get("districtPrices")
    if not isinstance(districts, list):
        raise DataValidationError("districtPrices must be a list")
    validate_candidate(candidate, districts, previous=None)


def _validate_price_map(name: str, prices: Any, *, minimum_classes: int = 4) -> None:
    if not isinstance(prices, dict) or len(prices) < minimum_classes:
        raise DataValidationError(f"{name} has no complete class price map")
    for vehicle_class, amount in prices.items():
        if str(vehicle_class) not in {"1", "2", "3", "4", "5", "6"}:
            raise DataValidationError(f"{name} has invalid vehicle class {vehicle_class}")
        if not isinstance(amount, (int, float)) or not 5 <= amount <= 100000:
            raise DataValidationError(f"{name} has implausible amount {amount}")


def _validate_multiplier_map(name: str, multipliers: Any) -> None:
    if not isinstance(multipliers, dict) or len(multipliers) < 4:
        raise DataValidationError(f"{name} has no complete class multiplier map")
    for vehicle_class, multiplier in multipliers.items():
        if str(vehicle_class) not in {"1", "2", "3", "4", "5", "6"}:
            raise DataValidationError(f"{name} has invalid vehicle class {vehicle_class}")
        if not isinstance(multiplier, (int, float)) or not 0.01 <= multiplier <= 100:
            raise DataValidationError(f"{name} has implausible multiplier {multiplier}")


def validate_toll_file(path: Path) -> None:
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise DataValidationError(f"{path.name} must be an object")
    seen_ids: set[str] = set()
    for field in ("fixedPoints", "corridors"):
        entries = payload.get(field)
        if not isinstance(entries, list) or not entries:
            raise DataValidationError(f"{path.name} has no {field}")
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                raise DataValidationError(f"{path.name} has an unnamed {field} entry")
            if entry["id"] in seen_ids:
                raise DataValidationError(f"{path.name} duplicates id {entry['id']}")
            seen_ids.add(entry["id"])
            name = f"{path.name}:{entry['id']}"
            if field == "fixedPoints":
                prices = entry.get("prices")
                if isinstance(prices, dict) and all(isinstance(value, dict) for value in prices.values()):
                    for period, period_prices in prices.items():
                        _validate_price_map(f"{name}:{period}", period_prices, minimum_classes=2)
                else:
                    _validate_price_map(name, prices)
                continue
            headline = entry.get("headlineFullTransitPrices")
            if isinstance(headline, dict):
                _validate_price_map(name, headline)
            elif isinstance(entry.get("kmMultipliers"), dict):
                _validate_multiplier_map(f"{name}:kmMultipliers", entry["kmMultipliers"])
            elif isinstance(entry.get("matrix"), dict):
                maps = [prices for destinations in entry["matrix"].values() if isinstance(destinations, dict) for prices in destinations.values()]
                if not maps:
                    raise DataValidationError(f"{name} matrix has no prices")
                for prices in maps:
                    _validate_price_map(f"{name}:matrix", prices)
            else:
                raise DataValidationError(f"{name} has no usable price model")


def validate_manifest(root: Path) -> None:
    manifest = load_json(root / "pricing_manifest.json")
    entries = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(entries, list) or not entries:
        raise DataValidationError("pricing_manifest.json has no file entries")
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("filename"), str):
            raise DataValidationError("manifest entry has no filename")
        path = root / entry["filename"]
        if not path.is_file():
            raise DataValidationError(f"manifest file is missing: {entry['filename']}")
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if entry.get("sha256") != actual_hash or entry.get("bytes") != path.stat().st_size:
            raise DataValidationError(f"manifest integrity mismatch: {entry['filename']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--require-fuel", action="store_true")
    parser.add_argument("--require-tolls", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if args.require_fuel:
        validate_fuel(root)
    if args.require_tolls:
        validate_toll_file(root / "tolls_v3_app_ready.json")
        validate_toll_file(root / "toll_matrix_tr_v1.json")
    validate_manifest(root)
    print("Semantic pricing validation passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (DataValidationError, KeyError, ValueError) as error:
        print(f"VALIDATION FAILED: {error}", file=sys.stderr)
        sys.exit(1)
