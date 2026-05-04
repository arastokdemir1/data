#!/usr/bin/env python3
"""
Generate a manifest for every remote data file consumed by CarLog.

The manifest lets the app and the operator know which files are current, when
they were generated, and whether a file is live-updated or tariff/manual sourced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


KNOWN_FILES = {
    "fuel_prices.json": {
        "category": "fuel",
        "priority": 10,
        "refreshPolicy": "automatic_15_minutes",
        "sourceType": "provider_api",
        "appRole": "current_compact_fuel_prices",
    },
    "fuel_prices_tr_v1.json": {
        "category": "fuel",
        "priority": 20,
        "refreshPolicy": "automatic_15_minutes",
        "sourceType": "provider_api",
        "appRole": "city_district_fuel_prices",
    },
    "toll_matrix_tr_v1.json": {
        "category": "toll",
        "priority": 10,
        "refreshPolicy": "source_change_or_manual_tariff_update",
        "sourceType": "official_tariff_matrix",
        "appRole": "primary_toll_matrix",
    },
    "tolls_v3_app_ready.json": {
        "category": "toll",
        "priority": 20,
        "refreshPolicy": "source_change_or_manual_tariff_update",
        "sourceType": "official_tariff_fallback",
        "appRole": "fallback_toll_tariff",
    },
    "vehicle_profiles_tr_v1.json": {
        "category": "vehicle",
        "priority": 10,
        "refreshPolicy": "catalog_release_update",
        "sourceType": "curated_vehicle_catalog",
        "appRole": "vehicle_consumption_profiles",
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def pick_last_updated(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("lastUpdated", "lastUpdate", "generatedAt", "updatedAt"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def pick_version(payload: Any) -> str | None:
    if isinstance(payload, dict):
        value = payload.get("version")
        if isinstance(value, str) and value:
            return value
    return None


def build_entry(root: Path, filename: str, meta: dict[str, Any]) -> dict[str, Any] | None:
    path = root / filename
    if not path.exists():
        return None

    payload = load_json(path)
    return {
        "id": filename.removesuffix(".json"),
        "filename": filename,
        "category": meta["category"],
        "priority": meta["priority"],
        "appRole": meta["appRole"],
        "sourceType": meta["sourceType"],
        "refreshPolicy": meta["refreshPolicy"],
        "version": pick_version(payload),
        "lastUpdated": pick_last_updated(payload),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".", help="Data repository root")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    entries = [
        entry
        for filename, meta in KNOWN_FILES.items()
        if (entry := build_entry(root, filename, meta)) is not None
    ]

    if not entries:
        raise RuntimeError("No known data files found; cannot generate manifest")

    manifest = {
        "version": "pricing-manifest-v1",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "repository": "arastokdemir1/data",
        "baseRawUrl": "https://raw.githubusercontent.com/arastokdemir1/data/main/",
        "refreshCadence": "15_minutes_for_provider_api_files",
        "files": sorted(entries, key=lambda item: (item["category"], item["priority"], item["filename"])),
    }

    write_json(root / "pricing_manifest.json", manifest)
    print(f"Generated pricing_manifest.json with {len(entries)} file entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
