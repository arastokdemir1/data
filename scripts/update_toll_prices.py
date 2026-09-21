#!/usr/bin/env python3
"""CarLog — Otomatik gişe fiyatı güncelleyici (resmî KGM PDF tabloları)."""
from __future__ import annotations
import argparse, io, json, re, subprocess, sys, time
from datetime import date
from pathlib import Path

def ensure_deps():
    try:
        import requests, pdfplumber  # noqa
    except ImportError:
        subprocess.run([sys.executable,"-m","pip","install",
                        "requests","beautifulsoup4","pdfplumber","-q"], check=True)
ensure_deps()
import requests
import pdfplumber

HEADERS = {"User-Agent": "Mozilla/5.0 (CarLogBot/2.0)"}
TODAY   = date.today().isoformat()
YEAR    = date.today().year

# This updater only owns these official sources. A missing one is unsafe: it
# would otherwise publish a mixture of fresh and stale tariffs as a success.
EXPECTED_FIXED_POINT_IDS = {
    "15_temmuz_sehitler_koprusu", "fatih_sultan_mehmet_koprusu",
    "yavuz_sultan_selim_koprusu", "osmangazi_koprusu", "1915_canakkale_koprusu",
}
EXPECTED_CORRIDOR_IDS = {
    "kmo_anadolu_kurtkoy_akyazi", "kmo_avrupa_kinali_odayeri", "ankara_nigde_o21",
    "malkara_canakkale_1915", "aydin_denizli", "izmir_aydin_o31", "izmir_cesme_o32",
}
BRIDGE_PDF_URL = (
    "https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/"
    f"OtoyolKopruUcret/{YEAR}Gecis_Ucret/1-15Temmuz-FSM.pdf"
)
OFFICIAL_SOURCE_BY_ID = {
    "15_temmuz_sehitler_koprusu": BRIDGE_PDF_URL,
    "fatih_sultan_mehmet_koprusu": BRIDGE_PDF_URL,
}

# ── Yardımcı fonksiyonlar ────────────────────────────────────────────────────
def clean_cell(s) -> str:
    s = str(s or "").strip()
    s = re.sub(r"[​‌‍﻿\xa0​]", "", s)  # sıfır genişlik karakterler
    s = s.replace("₺", "").strip()
    return s

def cell_to_float(s: str) -> float | None:
    s = clean_cell(s)
    if not s: return None
    # Tüm boşlukları kaldır (₺ 1 .010,00 → 1.010,00)
    s = re.sub(r"\s+", "", s)
    # Türkçe format: 1.234,56 → 1234.56
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        v = float(s)
        return v if 5 <= v <= 100000 else None
    except:
        return None

# ── 1. PDF kaynakları ────────────────────────────────────────────────────────
PDF_SOURCES = [
    # Aynı resmî tarife iki Boğaz köprüsünde de uygulanıyor. Eski HTML
    # adresi 2026'da 404 vermeye başladığı için doğrudan KGM PDF'i kullanılır.
    {"kind":"fixedPoint","ids":["15_temmuz_sehitler_koprusu", "fatih_sultan_mehmet_koprusu"],
     "label":"15 Temmuz + FSM","extract":"bridge_current","urls":[
      BRIDGE_PDF_URL,
      f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR-1}Gecis_Ucret/1-15Temmuz-FSM.pdf"]},
    # Sabit nokta köprüler (tek fiyat sütunu)
    {"kind":"fixedPoint","id":"yavuz_sultan_selim_koprusu","label":"YSS",
     "extract":"simple","urls":[
      f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR}Gecis_Ucret/3-YSSKoprusu.pdf",
      f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR-1}Gecis_Ucret/3-YSSKoprusu.pdf"]},
    {"kind":"fixedPoint","id":"osmangazi_koprusu","label":"Osmangazi",
     "extract":"simple","urls":[
      f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR}Gecis_Ucret/2-Osmangazi.pdf",
      f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR-1}Gecis_Ucret/2-Osmangazi.pdf"]},
    {"kind":"fixedPoint","id":"1915_canakkale_koprusu","label":"1915 Çanakkale",
     "extract":"simple","urls":[
      f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR}Gecis_Ucret/4-1915Canakkale.pdf",
      f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR-1}Gecis_Ucret/4-1915Canakkale.pdf"]},
    # Koridor matris tabloları
    {"kind":"corridor","id":"kmo_anadolu_kurtkoy_akyazi","label":"KMO Anadolu",
     "extract":"matrix","urls":[
      f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR}Gecis_Ucret/15-KMOAnadoluKurtkoy-Akyazi.pdf",
      f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR-1}Gecis_Ucret/15-KMOAnadoluKurtkoy-Akyazi.pdf"]},
    {"kind":"corridor","id":"kmo_avrupa_kinali_odayeri","label":"KMO Avrupa",
     "extract":"matrix","urls":[
      f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR}Gecis_Ucret/14-KMOAvrupaKinali-Odayeri.pdf",
      f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR-1}Gecis_Ucret/14-KMOAvrupaKinali-Odayeri.pdf"]},
    {"kind":"corridor","id":"ankara_nigde_o21","label":"Ankara-Niğde O-21",
     "extract":"matrix","urls":[
      f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR}Gecis_Ucret/17-Ankara-Nigde.pdf",
      f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR-1}Gecis_Ucret/17-Ankara-Nigde.pdf"]},
    {"kind":"corridor","id":"malkara_canakkale_1915","label":"Malkara-Çanakkale",
     "extract":"matrix","urls":[
      f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR}Gecis_Ucret/18-Malkara-Canakkale.pdf",
      f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR-1}Gecis_Ucret/18-Malkara-Canakkale.pdf"]},
    {"kind":"corridor","id":"aydin_denizli","label":"Aydın-Denizli",
     "extract":"matrix","urls":[
      f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR}Gecis_Ucret/19-Aydin-Denizli.pdf",
      f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR-1}Gecis_Ucret/19-Aydin-Denizli.pdf"]},
    {"kind":"corridor","id":"izmir_aydin_o31","label":"İzmir-Aydın O-31",
     "extract":"matrix","urls":[
      f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR}Gecis_Ucret/7-Izmir-Aydin.pdf",
      f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR-1}Gecis_Ucret/7-Izmir-Aydin.pdf"]},
    {"kind":"corridor","id":"izmir_cesme_o32","label":"İzmir-Çeşme O-32",
     "extract":"matrix","urls":[
      f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR}Gecis_Ucret/6-Izmir-Cesme.pdf",
      f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR-1}Gecis_Ucret/6-Izmir-Cesme.pdf"]},
]

def download_pdf(
    urls: list[str],
    *,
    attempts: int = 3,
    getter=requests.get,
    sleeper=time.sleep,
) -> bytes | None:
    for url in urls:
        for attempt in range(attempts):
            try:
                r = getter(url, headers=HEADERS, timeout=30)
                if r.status_code == 200 and len(r.content) > 2000:
                    return r.content
                # 404 gibi kalıcı cevapta aynı adresi tekrar tekrar deneme;
                # bir önceki yılın yedeğine geç.
                if 400 <= r.status_code < 500 and r.status_code != 429:
                    break
            except requests.RequestException:
                pass
            if attempt + 1 < attempts:
                sleeper(2 ** attempt)
    return None

def extract_price_rows(rows: list, *, highest_numeric: bool = False) -> dict | None:
    """Sınıf satırlarından fiyat çıkarır.

    KGM'nin 15 Temmuz/FSM PDF'inde güncel fiyatın yanında eski tarife/fark
    sütunu da bulunuyor. Güncel değer her satırdaki sayısal fiyatların en
    büyüğü; tek fiyat sütunlu PDF'lerde ise son hücre kullanılmaya devam eder.
    """
    prices: dict[str, float] = {}
    for row in rows:
        cells = [clean_cell(c) for c in row if c is not None]
        if len(cells) < 2: continue
        cls = cells[0]
        if not cls.isdigit() or not (1 <= int(cls) <= 6): continue
        values = [value for cell in cells[1:]
                  if (value := cell_to_float(cell)) is not None]
        if values:
            prices[cls] = max(values) if highest_numeric else values[-1]
    return prices if len(prices) >= 4 else None


def extract_simple(pdf_bytes: bytes) -> dict | None:
    """YSS, Osmangazi, 1915 gibi tek fiyat sütunlu köprüler."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for table in (page.extract_tables() or []):
                if prices := extract_price_rows(table): return prices
    return None


def extract_bridge_current(pdf_bytes: bytes) -> dict | None:
    """15 Temmuz/FSM tablosunda güncel fiyatı eski yan sütundan ayırır."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for table in (page.extract_tables() or []):
                if prices := extract_price_rows(table, highest_numeric=True):
                    return prices
    return None

def extract_matrix(pdf_bytes: bytes) -> dict | None:
    """Matris tabloları: her sınıf için max değer = tam geçiş fiyatı."""
    class_max: dict[str, float] = {}
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for table in (page.extract_tables() or []):
                for row in table:
                    cells = [clean_cell(c) for c in row if c is not None]
                    # Çok satırlı hücreler (tüm sınıflar bir hücrede)
                    for i, cell_raw in enumerate(row or []):
                        c = clean_cell(cell_raw)
                        lines = c.split("\n")
                        classes = [l.strip() for l in lines
                                   if l.strip().isdigit() and 1 <= int(l.strip()) <= 6]
                        if len(classes) >= 4 and i + 1 < len(row or []):
                            plines = clean_cell(row[i+1]).split("\n")
                            for j, cl in enumerate(classes):
                                if j < len(plines):
                                    v = cell_to_float(plines[j])
                                    if v and (cl not in class_max or v > class_max[cl]):
                                        class_max[cl] = v
                    # Normal satır işleme
                    cls_idx = None
                    for i, c in enumerate(cells):
                        if c.isdigit() and 1 <= int(c) <= 6:
                            cls_idx = i; break
                    if cls_idx is None: continue
                    cls = cells[cls_idx]
                    vals = [v for c in cells[cls_idx+1:]
                            if (v := cell_to_float(c)) is not None]
                    if vals:
                        mv = max(vals)
                        if cls not in class_max or mv > class_max[cls]:
                            class_max[cls] = mv
    if len(class_max) < 4: return None
    if class_max.get("1", 0) > class_max.get("4", 99999): return None
    return {k: round(v, 2) for k, v in class_max.items()}

def scrape_pdfs() -> tuple[dict, dict]:
    fp, cor = {}, {}
    for src in PDF_SOURCES:
        print(f"PDF: {src['label']}...")
        data = download_pdf(src["urls"])
        if not data:
            print("  ⚠️  İndirilemedi"); continue
        fn = {
            "simple": extract_simple,
            "bridge_current": extract_bridge_current,
            "matrix": extract_matrix,
        }[src["extract"]]
        prices = fn(data)
        if prices:
            target_ids = src["ids"] if "ids" in src else [src["id"]]
            target = fp if src["kind"] == "fixedPoint" else cor
            for target_id in target_ids:
                target[target_id] = dict(prices)
                print(f"  ✅ {target_id}: {prices}")
        else:
            print("  ⚠️  Fiyat çıkarılamadı")
    return fp, cor

# ── 2. JSON güncelleme ───────────────────────────────────────────────────────
def update_v3(path: Path, fp_prices: dict, cor_prices: dict) -> bool:
    if not path.exists(): return False
    with open(path, encoding="utf-8") as f: data = json.load(f)
    changed = False
    for fp in data.get("fixedPoints", []):
        fid = fp.get("id", "")
        if fid in fp_prices and fp.get("prices") != fp_prices[fid]:
            fp["prices"] = fp_prices[fid]; changed = True
        official_source = OFFICIAL_SOURCE_BY_ID.get(fid)
        if official_source and fp.get("officialSource") != official_source:
            fp["officialSource"] = official_source; changed = True
    for cor in data.get("corridors", []):
        cid = cor.get("id", "")
        if cid in cor_prices and cor.get("headlineFullTransitPrices") != cor_prices[cid]:
            cor["headlineFullTransitPrices"] = cor_prices[cid]; changed = True
    if changed:
        data["lastUpdated"] = TODAY
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        print(f"✅ {path.name} güncellendi.")
    else:
        print(f"ℹ️  {path.name} değişmedi.")
    return changed


def remove_zero_cost_matrix_edges(path: Path) -> bool:
    """Farklı istasyonlar arasındaki sıfır ücretli bozuk kenarları kaldırır.

    Sıfırı gerçek bir fiyat gibi yayınlamak ücretsiz geçiş gösterir. Kenarı
    kaldırmak daha güvenlidir: uygulama kesin olmayan durumda kendi tahmini
    geri dönüşünü kullanır ve semantik doğrulama sıfır fiyatı kabul etmez.
    """
    if not path.exists(): return False
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    removed: list[str] = []
    for corridor in data.get("corridors", []):
        matrix = corridor.get("matrix")
        if not isinstance(matrix, dict): continue
        for source_id, destinations in matrix.items():
            if not isinstance(destinations, dict): continue
            for destination_id, prices in list(destinations.items()):
                if source_id == destination_id or not isinstance(prices, dict) or not prices:
                    continue
                values = list(prices.values())
                if all(isinstance(value, (int, float)) and value == 0 for value in values):
                    del destinations[destination_id]
                    removed.append(f"{corridor.get('id')}:{source_id}->{destination_id}")
    if not removed: return False
    data["lastUpdated"] = TODAY
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    print(f"⚠️  {path.name}: {len(removed)} sıfır ücretli bozuk matris kenarı kaldırıldı.")
    return True

def require_complete_scrape(fixed_prices: dict, corridor_prices: dict) -> None:
    missing_fixed = sorted(EXPECTED_FIXED_POINT_IDS - set(fixed_prices))
    missing_corridors = sorted(EXPECTED_CORRIDOR_IDS - set(corridor_prices))
    if missing_fixed or missing_corridors:
        raise RuntimeError(
            "Incomplete official toll scrape; no data will be published. "
            f"missing fixed={missing_fixed}, corridors={missing_corridors}"
        )


def regenerate_manifest(root: Path) -> None:
    subprocess.run(
        [sys.executable, str(Path(__file__).with_name("update_pricing_manifest.py")), "--root", str(root)],
        check=True,
    )

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--no-pdf", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve()

    pdf_fp, pdf_cor = ({}, {}) if args.no_pdf else scrape_pdfs()

    require_complete_scrape(pdf_fp, pdf_cor)

    changed = update_v3(root / "tolls_v3_app_ready.json", pdf_fp, pdf_cor)
    # KRITIK: uygulama gişe fiyatlarını önce toll_matrix_tr_v1.json'dan okuyor
    # ("primary_toll_matrix") — bu dosya güncellenmezse app'te eski fiyat gösterilir.
    # Önceden sadece app_ready dosyası güncelleniyordu, matrix hiç dokunulmuyordu.
    matrix_path = root / "toll_matrix_tr_v1.json"
    changed = update_v3(matrix_path, pdf_fp, pdf_cor) or changed
    changed = remove_zero_cost_matrix_edges(matrix_path) or changed
    if changed:
        regenerate_manifest(root)
    return 0

if __name__ == "__main__":
    sys.exit(main())
