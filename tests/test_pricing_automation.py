from __future__ import annotations

import json
import hashlib
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import update_fuel_prices_opet as fuel
import update_toll_prices as toll
from validate_pricing_data import validate_manifest, validate_toll_file


FIXTURES = ROOT / "tests" / "fixtures"


def districts(count: int = 70, price: float = 80.0) -> list[dict]:
    return [
        {
            "provinceCode": index, "provinceName": f"Province {index}",
            "districtName": f"District {index}", "gasoline": price, "diesel": price + 10,
        }
        for index in range(count)
    ]


class FakeResponse:
    status = 200

    def __init__(self, payload: bytes):
        self.payload = payload
        self.offset = 0

    def read(self, size: int = -1) -> bytes:
        if self.offset >= len(self.payload):
            return b""
        end = len(self.payload) if size < 0 else self.offset + size
        value = self.payload[self.offset:end]
        self.offset += len(value)
        return value

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FuelAutomationTests(unittest.TestCase):
    def test_retries_a_transient_download_failure(self):
        attempts = 0

        def opener(*_, **__):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("temporary outage")
            return FakeResponse(b'{"ok": true}')

        self.assertEqual(fuel.fetch_bytes("https://example.test", attempts=2, opener=opener, sleeper=lambda _: None), b'{"ok": true}')
        self.assertEqual(attempts, 2)

    def test_opet_fixture_builds_istanbul_compact_prices(self):
        source = json.loads((FIXTURES / "opet_allprices_valid.json").read_text())
        normalized = [fuel.normalize_district(row) for row in source]
        compact = fuel.build_compact_prices(normalized, 32.3, "2026-09-19T00:00:00Z")
        self.assertEqual(compact.gasoline, 80.23)
        self.assertEqual(compact.diesel, 96.18)

    def test_broken_opet_fixture_cannot_produce_a_publishable_payload(self):
        source = json.loads((FIXTURES / "opet_allprices_broken.json").read_text())
        normalized = [fuel.normalize_district(row) for row in source]
        with self.assertRaises(fuel.DataValidationError):
            fuel.build_compact_prices(normalized, 32.3, "2026-09-19T00:00:00Z")

    def test_aytemiz_fixture_is_a_real_gasoline_fallback_schema(self):
        original = fuel.MIN_PROVINCES
        fuel.MIN_PROVINCES = 3
        try:
            rows, timestamp = fuel.parse_aytemiz_prices((FIXTURES / "aytemiz_prices_valid.html").read_text())
        finally:
            fuel.MIN_PROVINCES = original
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["gasoline"], 80.16)
        self.assertEqual(rows[0]["diesel"], 96.12)
        self.assertEqual(timestamp, "2026-09-19T20:46:00Z")

    def test_rejects_stale_or_incomplete_or_large_change_data(self):
        stale = fuel.CompactPrices(80, 90, 32, "2026-09-01T00:00:00Z")
        with self.assertRaises(fuel.DataValidationError):
            fuel.validate_candidate(stale, districts(), previous=None, now=datetime(2026, 9, 19, tzinfo=timezone.utc))
        fresh = fuel.CompactPrices(130, 90, 32, "2026-09-19T00:00:00Z")
        with self.assertRaises(fuel.DataValidationError):
            fuel.validate_candidate(fresh, districts(), previous={"gasoline": 80, "diesel": 90, "lpg": 32}, now=datetime(2026, 9, 19, tzinfo=timezone.utc))
        with self.assertRaises(fuel.DataValidationError):
            fuel.validate_candidate(fuel.CompactPrices(80, 90, 32, "2026-09-19T00:00:00Z"), districts(2), previous=None, now=datetime(2026, 9, 19, tzinfo=timezone.utc))


class TollAutomationTests(unittest.TestCase):
    def test_partial_scrape_never_reaches_publish_step(self):
        with self.assertRaises(RuntimeError):
            toll.require_complete_scrape({}, {})

    def test_toll_matrix_rejects_bad_class_or_amount(self):
        payload = {
            "fixedPoints": [{"id": "bridge", "prices": {"1": 10, "2": 20, "3": 30, "4": 40}}],
            "corridors": [{"id": "road", "headlineFullTransitPrices": {"1": 10, "2": 20, "3": 30, "4": 40}}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "matrix.json"
            path.write_text(json.dumps(payload))
            validate_toll_file(path)
            payload["corridors"][0]["headlineFullTransitPrices"]["1"] = 0
            path.write_text(json.dumps(payload))
            with self.assertRaises(fuel.DataValidationError):
                validate_toll_file(path)

    def test_toll_matrix_rejects_zero_cost_between_distinct_stations(self):
        payload = {
            "fixedPoints": [{"id": "bridge", "prices": {"1": 10, "2": 20, "3": 30, "4": 40}}],
            "corridors": [{"id": "road", "matrix": {"entry": {"exit": {"1": 0, "2": 0, "3": 0, "4": 0}}}}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "matrix.json"
            path.write_text(json.dumps(payload))
            with self.assertRaises(fuel.DataValidationError):
                validate_toll_file(path)

    def test_manifest_detects_a_changed_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_file = root / "sample.json"
            data_file.write_text('{"version": 1}')
            payload = data_file.read_bytes()
            (root / "pricing_manifest.json").write_text(json.dumps({"files": [{
                "filename": "sample.json",
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }]}))
            validate_manifest(root)
            data_file.write_text('{"version": 2}')
            with self.assertRaises(fuel.DataValidationError):
                validate_manifest(root)


if __name__ == "__main__":
    unittest.main()
