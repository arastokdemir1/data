# CarLog Data Automation

Bu klasördeki dosyalar `arastokdemir1/data` GitHub reposuna kopyalanmak için hazırdır.

## Ne yapar?

- OPET'in public fiyat endpointinden yakıt fiyatlarını çeker.
- App'in şu an okuduğu `fuel_prices.json` dosyasını otomatik üretir.
- Daha detaylı kullanım için `fuel_prices_tr_v1.json` dosyasını da üretir.
- Tüm app-facing veri dosyaları için `pricing_manifest.json` üretir.
- GitHub Actions ile 15 dakikada bir otomatik çalışır ve değişiklik varsa commit atar.

## GitHub data reposuna kurulacak dosyalar

```text
.github/workflows/update-fuel-prices.yml
scripts/update_all_pricing_data.py
scripts/update_fuel_prices_opet.py
scripts/update_pricing_manifest.py
```

## Manuel test

```bash
python3 scripts/update_all_pricing_data.py --root .
python3 -m json.tool fuel_prices.json >/dev/null
python3 -m json.tool fuel_prices_tr_v1.json >/dev/null
python3 -m json.tool pricing_manifest.json >/dev/null
```

## Önemli not

OPET verisi tavsiye fiyat niteliğindedir. İstasyon pompa fiyatı küçük fark gösterebilir.
OPET endpointinde LPG görünmediği için script mevcut `fuel_prices.json` içindeki LPG değerini korur.

Gişe ücretleri yakıt gibi public JSON API ile yayınlanmıyor; çoğu resmi PDF/tablo veya işletmeci hesaplayıcısı olarak geliyor. Bu yüzden `toll_matrix_tr_v1.json` ve `tolls_v3_app_ready.json` ana GitHub veri dosyası olarak kalır, `pricing_manifest.json` ise app'e hangi dosyanın güncel olduğunu ve hash bilgisini taşır.
