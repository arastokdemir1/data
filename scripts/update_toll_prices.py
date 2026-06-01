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

# ── 1. FSM + 15T → KGM HTML ─────────────────────────────────────────────────
KGM_BRIDGE_URL = ("https://www.kgm.gov.tr/sayfalar/kgm/sitetr/otoyollar/"
                  "otoyolkopruucret/koprugecisucret.aspx")
BRIDGE_HTML_IDS = ["15_temmuz_sehitler_koprusu", "fatih_sultan_mehmet_koprusu"]

def parse_class_prices(rows: list[list[str]]) -> dict[str, float]:
    prices: dict[str, float] = {}
    for row in rows:
        if len(row) < 2: continue
        cls = row[0].replace("​","").strip()
        if not cls.isdigit() or not (1 <= int(cls) <= 6): continue
        ps = row[-1].replace("​","").replace("\xa0","").replace(" ","").replace(",",".")
        nums = re.findall(r"\d+(?:\.\d+)?", ps)
        if nums:
            try: prices[cls] = float(nums[0])
            except: pass
    return prices

def scrape_bridge_html() -> dict[str, dict]:
    print("KGM köprü HTML çekiliyor (FSM+15T)...")
    try:
        r = requests.get(KGM_BRIDGE_URL, headers=HEADERS, timeout=25); r.encoding = "utf-8"
    except Exception as e:
        print(f"  ⚠️  {e}"); return {}
    soup = BeautifulSoup(r.text, "html.parser")
    result: dict[str, dict] = {}
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [td.get_text(" ",strip=True).replace("​","").strip()
                     for td in tr.find_all(["td","th"])]
            cells = [c for c in cells if c]
            if cells: rows.append(cells)
        prices = parse_class_prices(rows)
        if len(prices) >= 4:
            for fid in BRIDGE_HTML_IDS:
                if fid not in result:
                    result[fid] = prices
                    print(f"  ✅ {fid}: {prices}")
            break
    return result

# ── 2. PDF kaynakları ────────────────────────────────────────────────────────
PDF_SOURCES = [
    {"kind":"fixedPoint","id":"yavuz_sultan_selim_koprusu","label":"YSS",
     "urls":[f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR}Gecis_Ucret/3-YSSKoprusu.pdf",
             f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR-1}Gecis_Ucret/3-YSSKoprusu.pdf"]},
    {"kind":"fixedPoint","id":"osmangazi_koprusu","label":"Osmangazi",
     "urls":[f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR}Gecis_Ucret/2-Osmangazi.pdf",
             f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR-1}Gecis_Ucret/2-Osmangazi.pdf"]},
    {"kind":"fixedPoint","id":"1915_canakkale_koprusu","label":"1915 Çanakkale",
     "urls":[f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR}Gecis_Ucret/4-1915Canakkale.pdf",
             f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR-1}Gecis_Ucret/4-1915Canakkale.pdf"]},
    {"kind":"corridor","id":"kmo_anadolu_kurtkoy_akyazi","label":"KMO Anadolu",
     "urls":[f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR}Gecis_Ucret/15-KMOAnadoluKurtkoy-Akyazi.pdf",
             f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR-1}Gecis_Ucret/15-KMOAnadoluKurtkoy-Akyazi.pdf"]},
    {"kind":"corridor","id":"kmo_avrupa_kinali_odayeri","label":"KMO Avrupa",
     "urls":[f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR}Gecis_Ucret/14-KMOAvrupaKinali-Odayeri.pdf",
             f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR-1}Gecis_Ucret/14-KMOAvrupaKinali-Odayeri.pdf"]},
    {"kind":"corridor","id":"ankara_nigde_o21","label":"Ankara-Niğde O-21",
     "urls":[f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR}Gecis_Ucret/17-Ankara-Nigde.pdf",
             f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR-1}Gecis_Ucret/17-Ankara-Nigde.pdf"]},
    {"kind":"corridor","id":"malkara_canakkale_1915","label":"Malkara-Çanakkale",
     "urls":[f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR}Gecis_Ucret/18-Malkara-Canakkale.pdf",
             f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR-1}Gecis_Ucret/18-Malkara-Canakkale.pdf"]},
    {"kind":"corridor","id":"aydin_denizli","label":"Aydın-Denizli",
     "urls":[f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR}Gecis_Ucret/19-Aydin-Denizli.pdf",
             f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR-1}Gecis_Ucret/19-Aydin-Denizli.pdf"]},
    {"kind":"corridor","id":"izmir_aydin_o31","label":"İzmir-Aydın O-31",
     "urls":[f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR}Gecis_Ucret/7-Izmir-Aydin.pdf",
             f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR-1}Gecis_Ucret/7-Izmir-Aydin.pdf"]},
    {"kind":"corridor","id":"izmir_cesme_o32","label":"İzmir-Çeşme O-32",
     "urls":[f"https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{YEAR}Gecis_Ucret/6-Izmir-Cesme.pdf",
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

def cell_to_float(s: str) -> float | None:
    """Türkçe ondalık formatını (1.234,56 veya 1234,56 veya 1234.56) float'a çevir."""
    s = s.strip().replace("​","").replace("\xa0","")
    # 1.234,56 → 1234.56
    if re.match(r"^\d{1,3}(\.\d{3})?,\d{1,2}$", s):
        s = s.replace(".","").replace(",",".")
    else:
        s = s.replace(",",".")
    try:
        v = float(s)
        return v if 5 <= v <= 50000 else None
    except: return None

def extract_full_transit_pdf(pdf_bytes: bytes) -> dict[str, float] | None:
    """
    KGM PDF'lerinden tam geçiş fiyatlarını çıkar.
    Strateji: tablo çekimi → her sınıf (1-6) için maksimum değer = tam geçiş.
    Tek fiyatlı köprülerde de max = tek değer olduğundan evrensel çalışır.
    """
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            all_tables = []
            for page in pdf.pages:
                all_tables.extend(page.extract_tables() or [])
    except Exception as e:
        print(f"    ⚠️  PDF açılamadı: {e}"); return None

    # Her sınıf için maksimum fiyat
    class_max: dict[str, float] = {}

    for table in all_tables:
        for row in table:
            if not row: continue
            cells = [str(c or "").strip() for c in row]

            # Sınıf sütununu bul (1-6 rakamı olan hücre)
            cls_idx = None
            for i, c in enumerate(cells):
                clean = c.replace("​","").strip()
                if clean.isdigit() and 1 <= int(clean) <= 6:
                    cls_idx = i
                    break
            if cls_idx is None: continue

            cls = cells[cls_idx].replace("​","").strip()
            # Geri kalan hücrelerdeki geçerli fiyatları topla
            vals = []
            for c in cells[cls_idx+1:]:
                v = cell_to_float(c)
                if v is not None:
                    vals.append(v)

            if not vals: continue
            max_val = max(vals)
            # Mantıklılık kontrolü: tek sınıf için maks değer
            if cls not in class_max or max_val > class_max[cls]:
                class_max[cls] = max_val

    if len(class_max) < 4:
        return None

    # Sınıf 1 < Sınıf 5 kontrolü (gerçekçi otoyol tarifesi)
    vals_sorted = [class_max.get(str(i)) for i in range(1,7) if str(i) in class_max]
    if len(vals_sorted) >= 2 and class_max.get("1", 0) > class_max.get("4", 9999):
        return None  # Ters sıralı = yanlış çekim

    return {k: round(v, 2) for k, v in class_max.items()}

def scrape_pdfs() -> tuple[dict[str,dict], dict[str,dict]]:
    fp, cor = {}, {}
    for src in PDF_SOURCES:
        print(f"PDF: {src['label']}...")
        data = download_pdf(src["urls"])
        if not data:
            print("  ⚠️  İndirilemedi"); continue
        prices = extract_full_transit_pdf(data)
        if prices:
            (fp if src["kind"]=="fixedPoint" else cor)[src["id"]] = prices
            print(f"  ✅ {src['id']}: {prices}")
        else:
            print("  ⚠️  Fiyat çıkarılamadı")
    return fp, cor

# ── 3. JSON güncelleme ────────────────────────────────────────────────────────
def update_v3(path: Path, fp_prices: dict, cor_prices: dict) -> bool:
    if not path.exists(): return False
    with open(path, encoding="utf-8") as f: data = json.load(f)
    changed = False
    for fp in data.get("fixedPoints", []):
        fid = fp.get("id","")
        if fid in fp_prices and fp.get("prices") != fp_prices[fid]:
            fp["prices"] = fp_prices[fid]; changed = True
    for cor in data.get("corridors", []):
        cid = cor.get("id","")
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
    with open(mp, "w", encoding="utf-8") as f: json.dump(m, f, ensure_ascii=False, indent=2)
    print("✅ manifest güncellendi.")

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--no-pdf", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve()

    html_fp = scrape_bridge_html()
    pdf_fp, pdf_cor = ({},{}) if args.no_pdf else scrape_pdfs()
    all_fp = {**html_fp, **pdf_fp}

    if not all_fp and not pdf_cor:
        print("⚠️  Hiçbir fiyat alınamadı."); return 1

    update_v3(root / "tolls_v3_app_ready.json", all_fp, pdf_cor)
    update_manifest(root)
    return 0

if __name__ == "__main__":
    sys.exit(main())
