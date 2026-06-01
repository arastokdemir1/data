#!/usr/bin/env python3
"""CarLog — Otomatik gişe fiyatı güncelleyici (KGM HTML + PDF tablo çekimi)."""
from __future__ import annotations
import argparse, io, json, re, subprocess, sys
from datetime import date
from pathlib import Path

def ensure_deps():
    try:
        import requests, bs4, pdfplumber  # noqa
    except ImportError:
        subprocess.run([sys.executable,"-m","pip","install",
                        "requests","beautifulsoup4","pdfplumber","-q"], check=True)
ensure_deps()
import requests
from bs4 import BeautifulSoup
import pdfplumber

HEADERS = {"User-Agent": "Mozilla/5.0 (CarLogBot/2.0)"}
TODAY   = date.today().isoformat()
YEAR    = date.today().year

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

# ── 1. FSM + 15T → HTML ─────────────────────────────────────────────────────
KGM_BRIDGE_URL = ("https://www.kgm.gov.tr/sayfalar/kgm/sitetr/otoyollar/"
                  "otoyolkopruucret/koprugecisucret.aspx")
BRIDGE_HTML_IDS = ["15_temmuz_sehitler_koprusu", "fatih_sultan_mehmet_koprusu"]

def scrape_bridge_html() -> dict[str, dict]:
    print("KGM köprü HTML çekiliyor (FSM+15T)...")
    try:
        r = requests.get(KGM_BRIDGE_URL, headers=HEADERS, timeout=25); r.encoding = "utf-8"
    except Exception as e:
        print(f"  ⚠️  {e}"); return {}
    soup = BeautifulSoup(r.text, "html.parser")
    result: dict[str, dict] = {}
    for table in soup.find_all("table"):
        rows = [[td.get_text(" ",strip=True) for td in tr.find_all(["td","th"])]
                for tr in table.find_all("tr")]
        prices: dict[str, float] = {}
        for row in rows:
            cls = clean_cell(row[0]) if row else ""
            if not cls.isdigit() or not (1 <= int(cls) <= 6): continue
            v = cell_to_float(row[-1]) if len(row) > 1 else None
            if v: prices[cls] = v
        if len(prices) >= 4:
            for fid in BRIDGE_HTML_IDS:
                if fid not in result:
                    result[fid] = prices
                    print(f"  ✅ {fid}: {prices}")
            break
    return result

# ── 2. PDF kaynakları ────────────────────────────────────────────────────────
PDF_SOURCES = [
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

def download_pdf(urls: list[str]) -> bytes | None:
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200 and len(r.content) > 2000:
                return r.content
        except: pass
    return None

def extract_simple(pdf_bytes: bytes) -> dict | None:
    """YSS, Osmangazi, 1915 gibi tek fiyat sütunlu köprüler."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for table in (page.extract_tables() or []):
                prices: dict[str, float] = {}
                for row in table:
                    cells = [clean_cell(c) for c in row if c is not None]
                    if len(cells) < 2: continue
                    cls = cells[0]
                    if not cls.isdigit() or not (1 <= int(cls) <= 6): continue
                    v = cell_to_float(cells[-1])
                    if v: prices[cls] = v
                if len(prices) >= 4:
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
        fn = extract_simple if src["extract"] == "simple" else extract_matrix
        prices = fn(data)
        if prices:
            (fp if src["kind"] == "fixedPoint" else cor)[src["id"]] = prices
            print(f"  ✅ {src['id']}: {prices}")
        else:
            print("  ⚠️  Fiyat çıkarılamadı")
    return fp, cor

# ── 3. JSON güncelleme ───────────────────────────────────────────────────────
def update_v3(path: Path, fp_prices: dict, cor_prices: dict) -> bool:
    if not path.exists(): return False
    with open(path, encoding="utf-8") as f: data = json.load(f)
    changed = False
    for fp in data.get("fixedPoints", []):
        fid = fp.get("id", "")
        if fid in fp_prices and fp.get("prices") != fp_prices[fid]:
            fp["prices"] = fp_prices[fid]; changed = True
    for cor in data.get("corridors", []):
        cid = cor.get("id", "")
        if cid in cor_prices and cor.get("headlineFullTransitPrices") != cor_prices[cid]:
            cor["headlineFullTransitPrices"] = cor_prices[cid]; changed = True
    if changed:
        data["lastUpdated"] = TODAY
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"✅ {path.name} güncellendi.")
    else:
        print(f"ℹ️  {path.name} değişmedi.")
    return changed

def update_manifest(root: Path) -> None:
    mp = root / "pricing_manifest.json"
    if not mp.exists(): return
    with open(mp, encoding="utf-8") as f: m = json.load(f)
    m["generatedAt"] = f"{TODAY}T00:00:00Z"
    for e in m.get("files", []):
        if e.get("category") == "toll": e["lastUpdated"] = TODAY
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    print("✅ manifest güncellendi.")

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--no-pdf", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve()

    html_fp = scrape_bridge_html()
    pdf_fp, pdf_cor = ({}, {}) if args.no_pdf else scrape_pdfs()
    all_fp = {**html_fp, **pdf_fp}

    if not all_fp and not pdf_cor:
        print("⚠️  Hiçbir fiyat alınamadı."); return 1

    update_v3(root / "tolls_v3_app_ready.json", all_fp, pdf_cor)
    update_manifest(root)
    return 0

if __name__ == "__main__":
    sys.exit(main())
