#!/usr/bin/env python3
"""Resmî KGM/işletmeci PDF tarifelerinden istasyon matrisi çıkarır.

Neden ayrı modül: `update_toll_prices.py` yalnızca sınıf başına EN YÜKSEK fiyatı
("headline") okuyordu; matris hücrelerine hiç dokunmuyordu. Yıl ortası zamlarında
(01/07/2026) `toll_matrix_tr_v1.json` bu yüzden eski fiyatlarla kaldı ve bazı
koridorlar hiç doğru girilmemişti (1915 Çanakkale tablosu karışıktı).

Bu modül PDF tablolarını hücre hücre okur:
  • kare tablo  (YİD otoyolları): her giriş istasyonu için 6 sınıf satırı, her
    çıkış istasyonu için bir sütun; hücreler YÖNLÜDÜR (giriş → çıkış).
  • üçgen tablo (KGM otoyolları): yalnızca bir yarı dolu; boş yarı, karşı
    hücreden tamamlanır (tarife simetriktir).
Köşegen (aynı istasyon) hücresi "U dönüşü / en uzak mesafe" ücretidir; matrise
YAZILMAZ — uygulama aynı istasyonlu çifti kullanmıyor ve canlı algılamada
yanlış ücret üretirdi.

Her şey fail-closed: boyutlar beklenenle uyuşmazsa hata verilir, eski veri korunur.
"""
from __future__ import annotations

import io
import re
from typing import Any

CLASSES = ("1", "2", "3", "4", "5", "6")
_PRICE = re.compile(r"^\d{1,3}(?:\.\d{3})*,\d{2}$|^\d+,\d{2}$")


class MatrixExtractionError(RuntimeError):
    pass


def parse_price(cell: Any) -> float | None:
    """'₺ 1 .480,00' / '3 15,00' gibi bozuk boşluklu Türk fiyatını çevirir."""
    if cell is None:
        return None
    text = re.sub(r"[\s​‌‍﻿\xa0₺]", "", str(cell))
    text = text.replace("TL", "")
    if not _PRICE.match(text):
        return None
    value = float(text.replace(".", "").replace(",", "."))
    return value if value > 0 else None


def _class_of(cell: Any) -> str | None:
    text = re.sub(r"\s", "", str(cell or ""))
    return text if text in CLASSES else None


_HEADER_WORDS = ("ISTASYON", "SINIF", "CIKIS", "GIRIS", "GISELERI", "ARAC")


def normalize_name(value: Any) -> str:
    """Kırık/sarılmış istasyon adını karşılaştırılabilir hale getirir."""
    text = str(value or "")
    for source, target in (("İ", "I"), ("ı", "I"), ("Ş", "S"), ("ş", "S"), ("Ğ", "G"), ("ğ", "G"),
                           ("Ü", "U"), ("ü", "U"), ("Ö", "O"), ("ö", "O"), ("Ç", "C"), ("ç", "C")):
        text = text.replace(source, target)
    text = text.upper()
    text = re.sub(r"\d[\d.,]*\s*KM", "", text)  # "7,800 km" mesafe notları
    return re.sub(r"[^A-Z0-9]", "", text)


def _first_text(cells: list[Any], upto: int) -> str:
    for cell in cells[:upto]:
        text = re.sub(r"\s+", " ", str(cell or "")).strip()
        if text and not any(word in normalize_name(text) for word in _HEADER_WORDS):
            return text
    return ""


def extract_blocks(table: list[list[Any]]) -> list[dict[str, Any]]:
    """Bir tablodan giriş-istasyonu bloklarını çıkarır.

    Blok = birbirini izleyen 1..6 sınıf satırı; her satırın sınıf sütunundan sonraki
    hücreleri çıkış sütunlarıdır. Sınıf 1'e her dönüşte yeni blok başlar. Bloğun
    `"name"` anahtarı (sınıf olmayan bir sözlük girdisi) giriş istasyonunun adıdır.
    """
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    pending_name = ""
    for row in table:
        cells = list(row)
        class_index = next((i for i, cell in enumerate(cells) if _class_of(cell)), None)
        if class_index is None:
            text = _first_text(cells, len(cells))
            if text:
                pending_name = text
            continue
        values = [parse_price(cell) for cell in cells[class_index + 1:]]
        # Sınıf sütunundan sonra hiç fiyat yoksa (ör. "1" içeren başlık) atla.
        # Üçgen tabloda köşegen/ters yarı boş olabilir; ama satırın en az bir fiyatı vardır.
        if not any(value is not None for value in values):
            continue
        vehicle_class = _class_of(cells[class_index])
        if vehicle_class == "1" or current is None:
            own_name = _first_text(cells, class_index)
            current = {"name": own_name or pending_name}
            blocks.append(current)
            pending_name = ""
        current[vehicle_class] = values
    return blocks


def extract_tables(pdf_bytes: bytes) -> list[list[list[Any]]]:
    import pdfplumber

    tables: list[list[list[Any]]] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            tables.extend(page.extract_tables() or [])
    return tables


def build_matrix(
    cells: dict[tuple[int, int], dict[str, float]], station_ids: list[str]
) -> dict[str, dict[str, dict[str, float]]]:
    """Yönlü hücreleri matrise çevirir; boş yarıyı karşı hücreden tamamlar."""
    size = len(station_ids)
    matrix: dict[str, dict[str, dict[str, float]]] = {}
    for i in range(size):
        for j in range(size):
            if i == j:
                continue  # U dönüşü hücresi — yazılmaz
            price = cells.get((i, j)) or cells.get((j, i))
            if not price or set(price) != set(CLASSES):
                continue
            matrix.setdefault(station_ids[i], {})[station_ids[j]] = {
                vehicle_class: price[vehicle_class] for vehicle_class in CLASSES
            }
    return matrix


def extract_text_class_rows(pdf_bytes: bytes) -> list[dict[str, Any]]:
    """Tablo çıkarıcının bozduğu PDF'ler için `pdftotext -layout` satırlarından okur.

    (Aydın–Denizli: dikey "GİRİŞ GİŞELERİ" etiketi ve birleşik sütunlar yüzünden
    pdfplumber ikinci yarıdan sonra harf harf dağılıyor; poppler düzeni koruyor.)
    """
    import subprocess

    try:
        completed = subprocess.run(
            ["pdftotext", "-layout", "-", "-"], input=pdf_bytes,
            capture_output=True, check=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise MatrixExtractionError(f"pdftotext çalıştırılamadı: {error}") from error
    price_pattern = re.compile(r"(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2}")
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in completed.stdout.decode("utf-8", errors="replace").splitlines():
        match = re.match(r"^\s*(?:[^\d₺]*?\s)?([1-6])\s+₺", line)
        if not match:
            continue
        values = [parse_price(token) for token in price_pattern.findall(line[match.end() - 1:])]
        if not values:
            continue
        vehicle_class = match.group(1)
        if vehicle_class == "1" or current is None:
            current = {"name": ""}
            blocks.append(current)
        current[vehicle_class] = values
    return blocks


def _resolve_entry_indices(blocks: list[dict[str, Any]], names: list[str]) -> list[int]:
    """Her blok için istasyon dizinini bulur: önce ad eşleşmesi, sonra sıra."""
    import difflib

    normalized = [normalize_name(name) for name in names]
    # Blok sayısı istasyon sayısına eşitse sıra güvenilirdir (PDF'in dikey "GİRİŞ
    # GİŞELERİ" etiketi gibi gürültüler ad okumayı bozabiliyor).
    if len(blocks) == len(names):
        return list(range(len(names)))
    indices: list[int] = []
    used: set[int] = set()
    for block in blocks:
        key = normalize_name(block.get("name"))
        found = None
        if key in normalized and normalized.index(key) not in used:
            found = normalized.index(key)
        else:
            scored = sorted(
                ((difflib.SequenceMatcher(None, key, candidate).ratio(), position)
                 for position, candidate in enumerate(normalized) if position not in used),
                reverse=True,
            )
            if scored and scored[0][0] >= 0.85 and (len(scored) == 1 or scored[0][0] - scored[1][0] > 0.05):
                found = scored[0][1]
        if found is None:
            raise MatrixExtractionError(f"PDF giriş istasyonu eşleşmedi: {block.get('name')!r}")
        used.add(found)
        indices.append(found)
    return indices


def _cells_from_blocks(
    blocks: list[dict[str, Any]], names: list[str]
) -> dict[tuple[int, int], dict[str, float]]:
    size = len(names)
    entry_indices = _resolve_entry_indices(blocks, names)
    if len(set(entry_indices)) != len(entry_indices):
        raise MatrixExtractionError("iki PDF bloğu aynı istasyona eşleşti")
    cells: dict[tuple[int, int], dict[str, float]] = {}
    for entry, block in zip(entry_indices, blocks):
        if not all(vehicle_class in block for vehicle_class in CLASSES):
            raise MatrixExtractionError(f"{names[entry]}: sınıf satırları eksik")
        for vehicle_class in CLASSES:
            values = block[vehicle_class]
            if len(values) < size:
                values = values + [None] * (size - len(values))
            for exit_index in range(size):
                if values[exit_index] is not None:
                    cells.setdefault((entry, exit_index), {})[vehicle_class] = values[exit_index]
    return cells


def extract_corridor_matrix(
    pdf_bytes: bytes, stations: list[tuple[str, str]], *, text_rows: bool = False
) -> dict:
    """Tek tablolu (kare veya üçgen) koridor PDF'i için tam matris.

    `stations`: (id, görünen ad) listesi, PDF'teki ÇIKIŞ sütun sırasıyla aynı sırada.
    Giriş blokları ada göre eşlenir (üçgen tablolar ilk/son giriş satırını atlayabilir).
    """
    ids = [station_id for station_id, _ in stations]
    names = [name for _, name in stations]
    size = len(ids)
    if text_rows:
        candidates = [extract_text_class_rows(pdf_bytes)]
    else:
        candidates = [extract_blocks(table) for table in extract_tables(pdf_bytes)]
    candidates = [blocks for blocks in candidates if blocks]
    # Ana tablo: giriş bloğu sayısı istasyon sayısına en yakın ve satırı en geniş olan.
    plausible = [blocks for blocks in candidates
                 if size - 2 <= len(blocks) <= size and max(len(b["1"]) for b in blocks) == size]
    if not plausible:
        counts = [(len(blocks), max(len(b["1"]) for b in blocks)) for blocks in candidates]
        raise MatrixExtractionError(f"{size} sütunlu ana tablo bulunamadı (blok,sütun): {counts}")
    blocks = max(plausible, key=len)
    return build_matrix(_cells_from_blocks(blocks, names), ids)


# ── O-5 (Gebze–Orhangazi–İzmir): Osmangazi köprü gişesi + iki ayrı kesim tablosu ──────────
#
# Resmî tarife iki tablodur: Gebze–Bursa (1.-2. kesim) ve Bursa–İzmir (3.-4. kesim).
# Notlar (12_GOI_ENG.pdf):
#   • İstanbul→İzmir: köprü gişesinde YALNIZCA köprü ücreti, çıkış gişesinde kat edilen
#     otoyol ücreti alınır  → toplam = köprü + çıkış ücreti.
#   • İzmir→İstanbul: köprü gişesinde köprü ücreti + giriş noktasına göre otoyol ücreti
#     birlikte alınır      → tablodaki hücre zaten toplamdır.
# Uygulama eşleşen ilk ve son gişeden TOPLAM ücreti okuduğu için hücreler yönlü TOPLAM
# olarak yazılır. Kesimler arası geçişte toplam = giriş→Bursa Kuzey + Bursa Batı→çıkış
# (tarifenin bilinen değerleriyle doğrulandı: Osmangazi→İzmir 2895, →Turgutlu 2750).

O5_WEST_ENTRIES = ("osmangazi_koprusu_izmir_yonu", "altinova", "kilic", "orhangazi",
                   "gemlik", "bursa_serbest_bolge", "bursa_kuzey")
O5_WEST_EXITS = ("osmangazi_koprusu_izmir_yonu", "osmangazi_koprusu_istanbul_yonu",
                 "altinova", "kilic", "orhangazi", "gemlik", "bursa_serbest_bolge", "bursa_kuzey")
O5_BRIDGE_ISTANBUL_SIDE = "osmangazi_koprusu_izmir_yonu"   # İstanbul→İzmir köprü gişesi
O5_BRIDGE_IZMIR_SIDE = "osmangazi_koprusu_istanbul_yonu"   # İzmir→İstanbul köprü gişesi
O5_JUNCTION_WEST = "bursa_kuzey"
O5_JUNCTION_EAST = "bursa_batı"


def _table_cells(blocks: list[dict[str, Any]], entries: tuple[str, ...], exits: tuple[str, ...]):
    if len(blocks) != len(entries):
        raise MatrixExtractionError(f"O-5: {len(entries)} giriş bloğu beklendi, {len(blocks)} bulundu")
    cells: dict[tuple[str, str], dict[str, float]] = {}
    for entry, block in zip(entries, blocks):
        if not all(vehicle_class in block for vehicle_class in CLASSES):
            raise MatrixExtractionError(f"O-5 {entry}: sınıf satırları eksik")
        for vehicle_class in CLASSES:
            values = block[vehicle_class]
            if len(values) != len(exits):
                raise MatrixExtractionError(
                    f"O-5 {entry}: {len(exits)} çıkış sütunu beklendi, {len(values)} bulundu")
            for exit_id, value in zip(exits, values):
                if value is not None:
                    cells.setdefault((entry, exit_id), {})[vehicle_class] = value
    return cells


def extract_o5_matrix(pdf_bytes: bytes, station_ids: list[str]) -> dict:
    tables = [blocks for blocks in (extract_blocks(t) for t in extract_tables(pdf_bytes)) if blocks]
    east_ids = tuple(station_ids[station_ids.index("bursa_bati"):])
    west_table = next((b for b in tables if len(b) == len(O5_WEST_ENTRIES)
                       and len(b[0]["1"]) == len(O5_WEST_EXITS)), None)
    east_table = next((b for b in tables if len(b) == len(east_ids)
                       and len(b[0]["1"]) == len(east_ids)), None)
    if west_table is None or east_table is None:
        raise MatrixExtractionError("O-5: Gebze–Bursa veya Bursa–İzmir tablosu bulunamadı")
    west = _table_cells(west_table, O5_WEST_ENTRIES, O5_WEST_EXITS)
    east = _table_cells(east_table, east_ids, east_ids)
    for expected in (set(O5_WEST_ENTRIES) | set(O5_WEST_EXITS) | set(east_ids)):
        if expected not in station_ids:
            raise MatrixExtractionError(f"O-5: istasyon kimliği yok: {expected}")

    bridge = west[(O5_BRIDGE_ISTANBUL_SIDE, O5_BRIDGE_ISTANBUL_SIDE)]   # köprü gişesi, U dönüşü hücresi = köprü ücreti
    junction_east = "bursa_bati"

    def add(*parts: dict[str, float]) -> dict[str, float]:
        return {vehicle_class: sum(part[vehicle_class] for part in parts) for vehicle_class in CLASSES}

    zero = {vehicle_class: 0.0 for vehicle_class in CLASSES}

    def west_to_junction(entry: str) -> dict[str, float]:
        return zero if entry == "bursa_kuzey" else west[(entry, "bursa_kuzey")]

    def junction_to_east(exit_id: str) -> dict[str, float]:
        return zero if exit_id == junction_east else east[(junction_east, exit_id)]

    def east_to_junction(entry: str) -> dict[str, float]:
        return zero if entry == junction_east else east[(entry, junction_east)]

    non_bridge_west = [s for s in O5_WEST_ENTRIES if s != O5_BRIDGE_ISTANBUL_SIDE]
    cells: dict[tuple[str, str], dict[str, float]] = {}

    for a in non_bridge_west:                       # batı kesimi içi, köprüsüz
        for b in non_bridge_west:
            if a != b and (a, b) in west:
                cells[(a, b)] = west[(a, b)]
    for a, b in east:                               # doğu kesimi içi
        if a != b:
            cells[(a, b)] = east[(a, b)]

    # İstanbul→İzmir: köprü gişesinden giriş
    for gate in (O5_BRIDGE_ISTANBUL_SIDE, O5_BRIDGE_IZMIR_SIDE):
        cells[(gate, O5_BRIDGE_IZMIR_SIDE if gate == O5_BRIDGE_ISTANBUL_SIDE else O5_BRIDGE_ISTANBUL_SIDE)] = bridge
        for b in non_bridge_west:
            cells[(gate, b)] = add(bridge, west[(O5_BRIDGE_ISTANBUL_SIDE, b)])
        for b in east_ids:
            cells[(gate, b)] = add(bridge, west[(O5_BRIDGE_ISTANBUL_SIDE, "bursa_kuzey")], junction_to_east(b))

    # İzmir→İstanbul: köprü gişesinde çıkış (tablo hücresi köprüyü içerir)
    for gate in (O5_BRIDGE_ISTANBUL_SIDE, O5_BRIDGE_IZMIR_SIDE):
        for a in non_bridge_west:
            cells[(a, gate)] = west[(a, O5_BRIDGE_IZMIR_SIDE)]
        for a in east_ids:
            cells[(a, gate)] = add(east_to_junction(a), west[("bursa_kuzey", O5_BRIDGE_IZMIR_SIDE)])

    # Kesimler arası, köprüsüz geçiş
    for a in non_bridge_west:
        for b in east_ids:
            cells[(a, b)] = add(west_to_junction(a), junction_to_east(b))
    for a in east_ids:
        for b in non_bridge_west:
            cells[(a, b)] = add(east_to_junction(a), west[("bursa_kuzey", b)] if b != "bursa_kuzey" else zero)

    matrix: dict[str, dict[str, dict[str, float]]] = {}
    for (a, b), price in cells.items():
        if a == b or set(price) != set(CLASSES) or any(v <= 0 for v in price.values()):
            continue
        matrix.setdefault(a, {})[b] = {vehicle_class: round(price[vehicle_class], 2) for vehicle_class in CLASSES}
    return matrix


def validate_matrix(corridor_id: str, matrix: dict[str, dict[str, dict[str, float]]]) -> None:
    """Okuma hatasını yakalayan bariz tutarlılık denetimi (fail-closed).

    Tarifelerde 1.–5. sınıf ücretleri azalmayan sırada olur (motosiklet ayrı). Bir
    hücrede bu sıra bozuksa büyük olasılıkla basamak/sütun kayması vardır.
    """
    for entry, row in matrix.items():
        for exit_id, cell in row.items():
            values = [cell[vehicle_class] for vehicle_class in ("1", "2", "3", "4", "5")]
            if any(values[i] > values[i + 1] for i in range(4)):
                raise MatrixExtractionError(
                    f"{corridor_id}: {entry}→{exit_id} sınıf ücretleri sıradışı {cell}")
