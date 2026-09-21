from __future__ import annotations

import json
import hashlib
import base64
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import update_fuel_prices_opet as fuel
import update_toll_prices as toll
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from validate_pricing_data import _canonical_manifest_payload, validate_manifest, validate_toll_file


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

    def test_stale_opet_falls_back_to_fresh_aytemiz(self):
        stale_opet = fuel.CompactPrices(80, 90, 32, "2026-09-01T00:00:00Z")
        fresh_aytemiz = fuel.CompactPrices(81, 91, 32, datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
        with patch.object(fuel, "fetch_opet_prices", return_value=(stale_opet, districts())), \
             patch.object(fuel, "fetch_aytemiz_prices", return_value=(districts(price=81), fresh_aytemiz.last_update)):
            compact, selected, source, _ = fuel.select_verified_prices(32, previous=None)
        self.assertEqual(compact, fresh_aytemiz)
        self.assertEqual(len(selected), 70)
        self.assertIn("Aytemiz", source)


class TollAutomationTests(unittest.TestCase):
    def test_zero_cost_edges_are_removed_instead_of_published(self):
        payload = {
            "lastUpdated": "old",
            "corridors": [{
                "id": "road",
                "matrix": {
                    "entry": {
                        "entry": {"1": 0, "2": 0, "3": 0, "4": 0},
                        "broken_exit": {"1": 0, "2": 0, "3": 0, "4": 0},
                        "paid_exit": {"1": 10, "2": 20, "3": 30, "4": 40},
                    }
                },
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "matrix.json"
            path.write_text(json.dumps(payload))
            self.assertTrue(toll.remove_zero_cost_matrix_edges(path))
            cleaned = json.loads(path.read_text())
        destinations = cleaned["corridors"][0]["matrix"]["entry"]
        self.assertIn("entry", destinations, "Aynı istasyon diyagonali korunmalı")
        self.assertNotIn("broken_exit", destinations)
        self.assertIn("paid_exit", destinations)

    def test_pdf_download_retries_a_transient_failure(self):
        attempts = 0

        class Response:
            status_code = 200
            content = b"%PDF" + (b"x" * 3000)

        def getter(*_, **__):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise toll.requests.ConnectionError("temporary outage")
            return Response()

        payload = toll.download_pdf(
            ["https://example.test/toll.pdf"],
            attempts=2,
            getter=getter,
            sleeper=lambda _: None,
        )
        self.assertIsNotNone(payload)
        self.assertEqual(attempts, 2)

    def test_bridge_pdf_uses_current_tariff_not_old_side_column(self):
        rows = [
            ["ARAÇ SINIF", "ARAÇ TİPİ", "KÖPRÜ GEÇİŞ ÜCRETİ", "Mevcut Tarife"],
            ["1", "İki akslı", "59,00", "8,75"],
            ["2", "İki akslı büyük", "75,00", "11,25"],
            ["3", "Üç akslı", "168,00", "24,5"],
            ["4", "Dört veya beş akslı", "333,00", "49"],
            ["5", "Altı akslı", "440,00", "65,25"],
            ["6", "Motosiklet", "25,00", "3,5"],
        ]
        self.assertEqual(
            toll.extract_price_rows(rows, highest_numeric=True),
            {"1": 59.0, "2": 75.0, "3": 168.0, "4": 333.0, "5": 440.0, "6": 25.0},
        )

    def test_shared_bridge_pdf_populates_both_fixed_points(self):
        source = {
            "kind": "fixedPoint",
            "ids": ["15_temmuz_sehitler_koprusu", "fatih_sultan_mehmet_koprusu"],
            "label": "15 Temmuz + FSM",
            "extract": "bridge_current",
            "urls": ["https://example.test/bridges.pdf"],
        }
        prices = {"1": 59.0, "2": 75.0, "3": 168.0, "4": 333.0}
        with patch.object(toll, "PDF_SOURCES", [source]), \
             patch.object(toll, "download_pdf", return_value=b"pdf"), \
             patch.object(toll, "extract_bridge_current", return_value=prices):
            fixed, corridors = toll.scrape_pdfs()
        self.assertEqual(corridors, {})
        self.assertEqual(fixed[source["ids"][0]], prices)
        self.assertEqual(fixed[source["ids"][1]], prices)

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

    def test_signed_manifest_rejects_a_manifest_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_file = root / "sample.json"
            data_file.write_text('{"version": 1}')
            payload = data_file.read_bytes()
            manifest = {"files": [{
                "filename": "sample.json", "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
            }]}
            private_key = Ed25519PrivateKey.generate()
            manifest["signature"] = {
                "algorithm": "ed25519",
                "keyId": "test",
                "value": base64.b64encode(private_key.sign(_canonical_manifest_payload(manifest))).decode(),
            }
            (root / "pricing_manifest.json").write_text(json.dumps(manifest))
            old_public_key = os.environ.get("PRICING_MANIFEST_PUBLIC_KEY")
            os.environ["PRICING_MANIFEST_PUBLIC_KEY"] = base64.b64encode(private_key.public_key().public_bytes_raw()).decode()
            try:
                validate_manifest(root, require_signature=True)
                manifest["generatedAt"] = "tampered"
                (root / "pricing_manifest.json").write_text(json.dumps(manifest))
                with self.assertRaises(fuel.DataValidationError):
                    validate_manifest(root, require_signature=True)
            finally:
                if old_public_key is None:
                    os.environ.pop("PRICING_MANIFEST_PUBLIC_KEY", None)
                else:
                    os.environ["PRICING_MANIFEST_PUBLIC_KEY"] = old_public_key


if __name__ == "__main__":
    unittest.main()
