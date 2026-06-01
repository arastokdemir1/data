#!/usr/bin/env python3
"""
CarLog — Otomatik gişe fiyatı güncelleyici.
KGM'nin resmi HTML sayfasından köprü ücretlerini çeker.
KGM PDF'lerinden koridor fiyatlarını çeker (pdfplumber ile).
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path
from datetime import date

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Gerekli paketler yükleniyor...")
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "requests", "beautifulsoup4", "pdfplumber", "-q"], check=True)
    import requests
    from bs4 import BeautifulSoup

try:
    import pdfplumber
    HAS_PDF = True
except ImportError:
    HAS_PDF = False

HEADERS = {"User-Agent": "CarLogDataBot/1.0"}
TODAY = date.today().isoformat()

# ─── KGM Köprü Ücretleri (HTML sayfası) ────────────────────────────────────
KGM_BRIDGE_URL = (
    "https://www.kgm.gov.tr/sayfalar/kgm/sitetr/otoyollar/"
    "otoyolkopruucret/koprugecisucret.aspx"
)

# Tablodaki Türkçe köprü adı → bizim fixed-point ID eşlemesi
BRIDGE_NAME_MAP = {
    "15 temmuz": "15_temmuz_sehitler_koprusu",
    "şehitler": "15_temmuz_sehitler_koprusu",
    "boğaziçi": "15_temmuz_sehitler_koprusu",
    "fatih sultan mehmet": "fatih_sultan_mehmet_koprusu",
    "fsm": "fatih_sultan_mehmet_koprusu",
    "yavuz sultan selim": "yavuz_sultan_selim_koprusu",
    "yss": "yavuz_sultan_selim_koprusu",
    "osmangazi": "osmangazi_koprusu",
    "1915 çanakkale": "1915_canakkale_koprusu",
    "çanakkale": "1915_canakkale_koprusu",
    "avrasya": "avrasya_tuneli",
}


def scrape_bridge_prices() -> dict[str, dict]:
    """KGM HTML sayfasından köprü ücretlerini çek. {fixed_point_id: {sınıf: fiyat}}"""
    print("KGM köprü sayfası çekiliyor...")
    try:
        r = requests.get(KGM_BRIDGE_URL, headers=HEADERS, timeout=20)
        r.encoding = "utf-8"
    except Exception as e:
        print(f"  ⚠️  Köprü sayfası alınamadı: {e}")
        return {}

    soup = BeautifulSoup(r.text, "html.parser")
    result: dict[str, dict] = {}

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for row in rows:
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if len(cells) < 4:
                continue
            name_cell = cells[0].lower()
            matched_id = None
            for keyword, fp_id in BRIDGE_NAME_MAP.items():
                if keyword in name_cell:
                    matched_id = fp_id
                    break
            if not matched_id:
                continue

            # Hücrelerdeki sayıları çek
            prices = {}
            class_idx = 1
            for cell in cells[1:]:
                text = cell.replace(".", "").replace(",", ".").strip()
                nums = re.findall(r"\d+(?:\.\d+)?", text)
                if nums and class_idx <= 6:
                    try:
                        prices[str(class_idx)] = float(nums[0])
                        class_idx += 1
                    except ValueError:
                        pass

            if len(prices) >= 3:
                result[matched_id] = prices
                print(f"  ✅ {matched_id}: {prices}")

    return result


# ─── KGM PDF'lerinden koridor fiyatları ────────────────────────────────────
# Her PDF'in URL'si ve hangi corridor ID'sine karşılık geldiği.
# URL'ler yıllık değişir — aktif yıl otomatik denenir.
CORRIDOR_PDF_SOURCES = [
    {
        "id": "kmo_anadolu_kurtkoy_akyazi",
        "label": "KMO Anadolu (Kurtköy-Akyazı)",
        "url_pattern": "https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{year}Gecis_Ucret/15-KMOAnadoluKurtkoy-Akyazi.pdf",
    },
    {
        "id": "kmo_avrupa_kinali_odayeri",
        "label": "KMO Avrupa (Kınalı-Odayeri)",
        "url_pattern": "https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{year}Gecis_Ucret/14-KMOAvrupaKinali-Odayeri.pdf",
    },
    {
        "id": "gdh_anatolia_o4",
        "label": "GDH Anatolia (O-4)",
        "url_pattern": "https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{year}Gecis_Ucret/GDH%20Anatolian%20Motorway%20%28%C3%87aml%C4%B1ca-Ak%C4%B1nc%C4%B1%20Section%29%20Toll%20Rates.pdf",
    },
    {
        "id": "gdh_europe_o3",
        "label": "GDH Europe (O-3)",
        "url_pattern": "https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{year}Gecis_Ucret/11_GDH_Europe_Motorway%28Mahmutbey_Edirne_Crossroads%29Toll_Rates.pdf",
    },
    {
        "id": "ankara_nigde_o21",
        "label": "Ankara-Niğde (O-21)",
        "url_pattern": "https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{year}Gecis_Ucret/17-Ankara-Nigde.pdf",
    },
    {
        "id": "izmir_aydin_o31",
        "label": "İzmir-Aydın (O-31)",
        "url_pattern": "https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{year}Gecis_Ucret/7-Izmir-Aydin.pdf",
    },
    {
        "id": "izmir_cesme_o32",
        "label": "İzmir-Çeşme (O-32)",
        "url_pattern": "https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{year}Gecis_Ucret/6-Izmir-Cesme.pdf",
    },
    {
        "id": "malkara_canakkale_1915",
        "label": "Malkara-Çanakkale (1915 dahil)",
        "url_pattern": "https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{year}Gecis_Ucret/18-Malkara-Canakkale.pdf",
    },
    {
        "id": "aydin_denizli",
        "label": "Aydın-Denizli",
        "url_pattern": "https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Otoyollar/OtoyolKopruUcret/{year}Gecis_Ucret/19-Aydin-Denizli.pdf",
    },
]


def try_download_pdf(url_pattern: str) -> bytes | None:
    """Güncel yıl ve bir önceki yılı dene, PDF'i indir."""
    year = date.today().year
    for y in [year, year - 1]:
        url = url_pattern.format(year=y)
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200 and len(r.content) > 1000:
                return r.content
        except Exception:
            pass
    return None


def extract_full_transit_from_pdf(pdf_bytes: bytes) -> dict[str, float] | None:
    """
    PDF'den 'tam geçiş' fiyatlarını çıkar.
    KGM PDF'lerinde genellikle son satır tam geçiş fiyatını içerir.
    Sınıf 1-6 fiyatları aranır.
    """
    if not HAS_PDF:
        return None
    import io
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            all_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:
        return None

    # "Toplam" veya "Tam Geçiş" içeren satırları ara
    prices: dict[str, float] = {}
    lines = all_text.split("\n")
    for line in reversed(lines):  # Son satırlar genelde tam geçiş
        nums = re.findall(r"[\d]+(?:[.,]\d+)?", line)
        floats = []
        for n in nums:
            try:
                floats.append(float(n.replace(",", ".")))
            except ValueError:
                pass
        # 4-6 sayı varsa ve 50 < değer < 10000 ise, muhtemelen fiyat satırı
        valid = [f for f in floats if 50 < f < 10000]
        if 4 <= len(valid) <= 7:
            for i, v in enumerate(valid[:6], start=1):
                prices[str(i)] = v
            break

    return prices if len(prices) >= 3 else None


def scrape_corridor_full_transit_prices() -> dict[str, dict]:
    """Her PDF kaynağından tam geçiş fiyatını çek."""
    if not HAS_PDF:
        print("⚠️  pdfplumber yüklü değil, PDF çekimi atlanıyor.")
        return {}

    result: dict[str, dict] = {}
    for source in CORRIDOR_PDF_SOURCES:
        cid = source["id"]
        label = source["label"]
        print(f"PDF çekiliyor: {label}...")
        pdf_bytes = try_download_pdf(source["url_pattern"])
        if not pdf_bytes:
            print(f"  ⚠️  İndirilemedi: {label}")
            continue
        prices = extract_full_transit_from_pdf(pdf_bytes)
        if prices:
            result[cid] = prices
            print(f"  ✅ {cid}: {prices}")
        else:
            print(f"  ⚠️  Fiyat çıkarılamadı: {label}")

    return result


# ─── JSON güncelleme ─────────────────────────────────────────────────────────

def update_tolls_v3(path: Path, bridge_prices: dict, corridor_prices: dict) -> bool:
    if not path.exists():
        print(f"⚠️  Dosya yok: {path}")
        return False

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    changed = False

    # Sabit noktalar (köprüler)
    for fp in data.get("fixedPoints", []):
        fid = fp.get("id", "")
        if fid in bridge_prices:
            new_prices = bridge_prices[fid]
            if fp.get("prices") != new_prices:
                fp["prices"] = new_prices
                changed = True

    # Koridor tam geçiş fiyatları
    for corridor in data.get("corridors", []):
        cid = corridor.get("id", "")
        if cid in corridor_prices:
            new_prices = corridor_prices[cid]
            if corridor.get("headlineFullTransitPrices") != new_prices:
                corridor["headlineFullTransitPrices"] = new_prices
                changed = True

    if changed:
        data["lastUpdated"] = TODAY
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"✅ {path.name} güncellendi.")
    else:
        print(f"ℹ️  {path.name} değişmedi.")

    return changed


def update_manifest(root: Path) -> None:
    manifest_path = root / "pricing_manifest.json"
    if not manifest_path.exists():
        return
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    manifest["generatedAt"] = f"{TODAY}T00:00:00Z"
    for entry in manifest.get("files", []):
        if entry.get("category") == "toll":
            entry["lastUpdated"] = TODAY
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print("✅ pricing_manifest.json güncellendi.")


def main() -> int:
    parser = argparse.ArgumentParser(description="CarLog gişe fiyatı güncelleyici")
    parser.add_argument("--root", default=".", help="Data repo kök dizini")
    parser.add_argument("--no-pdf", action="store_true", help="PDF çekimini atla")
    args = parser.parse_args()

    root = Path(args.root).resolve()

    bridge_prices = scrape_bridge_prices()
    corridor_prices = {} if args.no_pdf else scrape_corridor_full_transit_prices()

    if not bridge_prices and not corridor_prices:
        print("⚠️  Hiçbir fiyat alınamadı, JSON'lar güncellenmedi.")
        return 1

    v3_path = root / "tolls_v3_app_ready.json"
    update_tolls_v3(v3_path, bridge_prices, corridor_prices)
    update_manifest(root)

    return 0


if __name__ == "__main__":
    sys.exit(main())
