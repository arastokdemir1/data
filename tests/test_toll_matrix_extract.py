"""Resmî tarife PDF'lerinden istasyon matrisi çıkarımının regresyon testleri.

Fixture'lar KGM'nin 01/07/2026 tarifeleridir (tests/fixtures/tariffs). Beklenen değerler
PDF'ten elle okunmuştur; bu testler eski hataların geri gelmesini engeller:
  • 1915 Çanakkale tablosu karışıktı (G4→G5 köprü geçişi "tarife yok" çıkıyordu),
  • YİD otoyollarının hücreleri 01/07 zammından sonra eski kalıyordu,
  • O-5'te İstanbul→İzmir yönünde köprü + çıkış ücreti ayrı alınır (toplam farklı).
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import toll_matrix_extract as extract
import update_toll_prices as toll

FIXTURES = ROOT / "tests" / "fixtures" / "tariffs"
HAS_PDFTOTEXT = shutil.which("pdftotext") is not None


def pdf(name: str) -> bytes:
    return (FIXTURES / f"{name}.pdf").read_bytes()


def matrix_of(name: str, stations: list[tuple[str, str]], **kwargs):
    return extract.extract_corridor_matrix(pdf(name), stations, **kwargs)


MALKARA = [
    ("malkara_g_1", "MALKARA G-1"), ("kavakkoy_g_2", "KAVAKKÖY G-2"),
    ("gelibolu_kuzey_g_3", "GELİBOLU KUZEY G-3"), ("gelibolu_guney_g_4", "GELİBOLU GÜNEY G-4"),
    ("1915_canakkale_koprusu_g_5", "1915 ÇANAKKALE KÖPRÜSÜ G-5"),
]
KMO_AVRUPA = [(sid, sid.upper()) for sid in
              ("kinali", "silivri", "catalca", "nakkas", "yassioren", "tayakadin", "fatih")]


class PriceParsingTests(unittest.TestCase):
    def test_broken_spacing_and_lira_sign(self):
        self.assertEqual(extract.parse_price("₺ 1 .480,00"), 1480.0)
        self.assertEqual(extract.parse_price("3 15,00"), 315.0)
        self.assertEqual(extract.parse_price("₺ 9 25,00"), 925.0)
        self.assertEqual(extract.parse_price("1.170,00"), 1170.0)

    def test_non_prices_are_not_prices(self):
        for value in (None, "", "7,800 km", "0,00", "GEBZE", "-"):
            self.assertIsNone(extract.parse_price(value), value)


class Malkara1915Tests(unittest.TestCase):
    def setUp(self):
        self.matrix = matrix_of("malkara_canakkale_1915", MALKARA)

    def test_bridge_crossing_is_the_official_1170(self):
        # Eski veri bu çifti hiç içermiyordu; uygulama ~281 ₺ tahmin yazıyordu.
        self.assertEqual(self.matrix["gelibolu_guney_g_4"]["1915_canakkale_koprusu_g_5"]["1"], 1170.0)
        self.assertEqual(self.matrix["1915_canakkale_koprusu_g_5"]["gelibolu_guney_g_4"]["1"], 1170.0)

    def test_full_transit_and_directional_cells(self):
        cell = self.matrix["malkara_g_1"]["1915_canakkale_koprusu_g_5"]
        self.assertEqual(cell, {"1": 1480.0, "2": 1960.0, "3": 3225.0, "4": 3705.0, "5": 6340.0, "6": 370.0})
        self.assertEqual(self.matrix["malkara_g_1"]["kavakkoy_g_2"]["1"], 175.0)
        self.assertEqual(self.matrix["kavakkoy_g_2"]["malkara_g_1"]["1"], 175.0)
        self.assertEqual(self.matrix["gelibolu_kuzey_g_3"]["gelibolu_guney_g_4"]["1"], 90.0)

    def test_every_pair_is_present_and_u_turn_is_not_published(self):
        ids = [station_id for station_id, _ in MALKARA]
        for a in ids:
            for b in ids:
                if a == b:
                    self.assertNotIn(b, self.matrix.get(a, {}))
                else:
                    self.assertIn(b, self.matrix[a], f"{a}→{b}")


class SquareDirectionalTests(unittest.TestCase):
    def test_kmo_avrupa_keeps_direction_and_july_prices(self):
        matrix = matrix_of("kmo_avrupa_kinali_odayeri", KMO_AVRUPA)
        # Giriş→çıkış yönü farklı fiyatlanır (Kınalı→Silivri 65, Silivri→Kınalı 105).
        self.assertEqual(matrix["kinali"]["silivri"]["1"], 65.0)
        self.assertEqual(matrix["silivri"]["kinali"]["1"], 105.0)
        self.assertEqual(matrix["kinali"]["catalca"]["1"], 165.0)


class TriangularTests(unittest.TestCase):
    def test_izmir_cesme_triangle_is_mirrored(self):
        stations = [(sid, sid.upper()) for sid in
                    ("seferihisar", "urla", "karaburun", "zeytinler", "alacati", "cesme")]
        matrix = matrix_of("izmir_cesme_o32", stations)
        self.assertEqual(sum(len(row) for row in matrix.values()), 30)
        for a in matrix:
            for b, cell in matrix[a].items():
                self.assertEqual(cell, matrix[b][a])


@unittest.skipUnless(HAS_PDFTOTEXT, "pdftotext (poppler-utils) gerekli")
class AydinDenizliTextModeTests(unittest.TestCase):
    def test_reads_every_cell_from_layout_text(self):
        names = ["AYDIN ALIN", "KÖŞK", "YENİ PAZAR", "NAZİLLİ", "KUYUCAK", "BUHARKENT",
                 "SARAYKÖY", "KUMKISIK-A", "KUMKISIK-B", "PAMUKKALE", "KOCABAŞ"]
        stations = [(name.lower().replace(" ", "_"), name) for name in names]
        matrix = matrix_of("aydin_denizli", stations, text_rows=True)
        ids = [station_id for station_id, _ in stations]
        self.assertEqual(sum(len(row) for row in matrix.values()), 110)
        self.assertEqual(matrix[ids[0]][ids[1]],
                         {"1": 90.0, "2": 110.0, "3": 135.0, "4": 155.0, "5": 175.0, "6": 20.0})
        self.assertEqual(matrix[ids[0]][ids[-1]]["1"], 590.0)


O5_IDS = ["osmangazi_koprusu_izmir_yonu", "osmangazi_koprusu_istanbul_yonu", "altinova", "kilic",
          "orhangazi", "gemlik", "bursa_serbest_bolge", "bursa_kuzey", "bursa_bati", "teknosab",
          "karacabey_mustafakemalpasa_1", "karacabey_mustafakemalpasa_2", "susurluk",
          "balikesir_kuzey", "balikesir_bati", "savastepe", "soma", "kirkagac", "akhisar",
          "saruhanli", "turgutlu", "izmir"]


class OsmangaziO5Tests(unittest.TestCase):
    def setUp(self):
        self.matrix = extract.extract_o5_matrix(pdf("gebze_orhangazi_izmir_o5"), O5_IDS)
        self.istanbul_side = "osmangazi_koprusu_izmir_yonu"
        self.izmir_side = "osmangazi_koprusu_istanbul_yonu"

    def price(self, a, b, vehicle_class="1"):
        return self.matrix[a][b][vehicle_class]

    def test_published_anchor_totals(self):
        # Bilinen resmî toplamlar (1. sınıf): köprü 1170, Bursa Kuzey 1540, Turgutlu 2750, İzmir 2895.
        self.assertEqual(self.price(self.istanbul_side, self.izmir_side), 1170.0)
        self.assertEqual(self.price(self.istanbul_side, "bursa_kuzey"), 1540.0)
        self.assertEqual(self.price(self.istanbul_side, "turgutlu"), 2750.0)
        self.assertEqual(self.price(self.istanbul_side, "izmir"), 2895.0)

    def test_bridge_fee_is_added_to_exit_fee_towards_izmir(self):
        # Köprü gişesinde yalnız köprü (1170), çıkışta Altınova ücreti (65) → 1235.
        self.assertEqual(self.price(self.istanbul_side, "altinova"), 1235.0)
        self.assertEqual(self.price(self.istanbul_side, "kilic"), 1170.0 + 105.0)

    def test_return_direction_charges_the_same_total(self):
        # İzmir→İstanbul: köprü gişesi hem köprüyü hem giriş ücretini alır (tablo hücresi toplamdır).
        self.assertEqual(self.price("altinova", self.izmir_side), 1235.0)
        self.assertEqual(self.price("izmir", self.izmir_side), 2895.0)
        self.assertEqual(self.price("altinova", self.istanbul_side), self.price("altinova", self.izmir_side))

    def test_no_bridge_between_stations_on_the_same_side(self):
        self.assertEqual(self.price("altinova", "kilic"), 65.0)
        self.assertEqual(self.price("bursa_bati", "teknosab"), 120.0)

    def test_sections_are_summed_across_bursa(self):
        # Altınova→Bursa Kuzey 345 + Bursa Batı→İzmir 1355 = 1700 (köprüsüz).
        self.assertEqual(self.price("altinova", "izmir"), 345.0 + 1355.0)

    def test_undefined_junction_hop_is_not_invented(self):
        self.assertNotIn("bursa_bati", self.matrix["bursa_kuzey"])
        self.assertNotIn("bursa_kuzey", self.matrix["bursa_bati"])

    def test_class_prices_are_ordered(self):
        extract.validate_matrix("o5", self.matrix)


class FailClosedTests(unittest.TestCase):
    def test_wrong_station_count_is_rejected(self):
        with self.assertRaises(extract.MatrixExtractionError):
            matrix_of("malkara_canakkale_1915", MALKARA[:3])

    def test_disordered_classes_are_rejected(self):
        with self.assertRaises(extract.MatrixExtractionError):
            extract.validate_matrix("x", {"a": {"b": {"1": 100.0, "2": 50.0, "3": 200.0,
                                                       "4": 300.0, "5": 400.0, "6": 10.0}}})


class UpdaterTests(unittest.TestCase):
    def write_matrix(self, directory: Path, matrix: dict) -> Path:
        path = directory / "toll_matrix_tr_v1.json"
        path.write_text(json.dumps({
            "version": "2026.5-matrix", "lastUpdated": "2026-01-01",
            "fixedPoints": [], "corridors": [{"id": "c", "label": "C", "stations": [], "matrix": matrix,
                                                "bundledFixedPointIds": [], "keywords": []}],
        }), encoding="utf-8")
        return path

    def test_changed_cells_are_written_and_version_bumped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_matrix(Path(directory), {"a": {"b": {"1": 10.0}}})
            new = {"a": {"b": {"1": 12.0, "2": 15.0, "3": 20.0, "4": 30.0, "5": 40.0, "6": 5.0}}}
            self.assertTrue(toll.update_matrix_cells(path, {"c": new}))
            written = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(written["corridors"][0]["matrix"], new)
            self.assertEqual(written["version"], "2026.6-matrix")

    def test_identical_cells_change_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            cells = {"a": {"b": {"1": 10.0}}}
            path = self.write_matrix(Path(directory), cells)
            before = path.read_text(encoding="utf-8")
            self.assertFalse(toll.update_matrix_cells(path, {"c": cells}))
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_bump_version(self):
        self.assertEqual(toll.bump_version("2026.5-matrix"), "2026.6-matrix")
        self.assertTrue(toll.bump_version("weird").endswith("-matrix"))


if __name__ == "__main__":
    unittest.main()
