# Telegram kullanıcı hesabıyla piyasa doğrulaması

Bu entegrasyon yalnız BIST projesine aittir. Projenin yayın botu üçüncü taraf
botla konuşmaz; yetkilendirilmiş Telegram **kullanıcı oturumu** en güçlü günlük
adayları `@borsabilgibot` üzerinden sorgular.

## Değişmez güvenlik kuralları

- İstanbul işlem günü başına en fazla 10 farklı sembol.
- Her sembol için 09:40–10:00 arasında en fazla bir teorik sorgu yapılır.
- Teorik veri trade kararını desteklemeye yetmezse 10:00–18:00 arasında en
  fazla bir derinlik iş akışı yapılır. `/derinlik` cevabında yalnız bir kez
  `Derinlik Görüntü Al` düğmesine basılır; canlı/yenilenen ekran açılmaz.
  Teorik yeterliyse derinlik alınmaz.
- Böylece günlük toplam 10–20 yazılı komut ve en fazla 10 görüntü düğmesi
  etkileşimi vardır. On sembollük havuz hiçbir koşulda aşılmaz.
- Timeout/hata ilgili aşamanın hakkını tüketir; otomatik retry yapılmaz.
- Havuza yalnız profesyonel puanı en az 88 olan son adaylar alınır.
- Görsel; sembol, fiyat, hacim, kademe toplamları ve gerçekleşen alan/satan
  satırlarıyla birlikte OCR edilir. Sembol eşleşmezse, yeterli kademe/işlem
  okunmazsa veya toplamlar tutarsızsa trade teyidi üretilmez.
- Teorik fiyat ve eşleşebilir miktar ayrıştırılmadıkça teorik teyit oluşmaz.

## Sunucu gizli ayarları

```dotenv
EXTERNAL_VERIFY_ENABLED=1
EXTERNAL_VERIFY_DAILY_LIMIT=10
EXTERNAL_VERIFY_MIN_SCORE=88
EXTERNAL_VERIFY_TARGET=borsabilgibot
EXTERNAL_VERIFY_THEORETICAL_COMMAND=/teorik {symbol}
EXTERNAL_VERIFY_DEPTH_COMMAND=/derinlik {symbol}
EXTERNAL_VERIFY_TIMEOUT=20

TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
TELEGRAM_EXPECTED_USER_ID=...
TELEGRAM_USER_SESSION=...
```

`DAILY_LIMIT` kod içinde de 10 ile üstten sınırlandırılır. Ortam değişkenine
daha büyük değer yazılması sınırı yükseltmez.

## Bir defalık kullanıcı oturumu

1. `https://my.telegram.org` üzerinden hesabınıza ait API ID ve API hash alın.
2. API bilgilerini GitHub Actions secret alanında `TELEGRAM_API_ID` ve
   `TELEGRAM_API_HASH` adlarıyla kaydedin.
3. `Configure Telegram Market Verifier` workflow'unu elle çalıştırın. Workflow
   değerleri loglamadan `/etc/bist-trading.env` içine atomik yazar ve kullanıcı
   oturumu tamamlanana kadar `EXTERNAL_VERIFY_ENABLED=0` bırakır.
4. Sunucuda `python scripts/create_telegram_user_session.py` çalıştırın.
5. Telefonunuza/Telegram hesabınıza gelen kodu doğrudan sunucu terminaline girin.
6. Üretilen `TELEGRAM_USER_SESSION` değerini yalnız sunucu secret/env alanına
   kaydedin. GitHub'a commit etmeyin.

### SSH anahtarı yanında değilse: tarayıcı akışı

1. GitHub Actions secret alanına telefonu E.164 biçiminde `TELEGRAM_PHONE`
   adıyla ekleyin (`+90...`).
2. `Telegram User Session - Send Code` iş akışını çalıştırın.
3. Telegram'a gelen tek kullanımlık kodu `TELEGRAM_LOGIN_CODE` secret'ı olarak
   ekleyin ve `Telegram User Session - Complete Login` iş akışını çalıştırın.
4. Tamamlama işi session'ı ekrana yazmadan `/etc/bist-trading.env` içine kurar,
   kullanıcı kimliğini sabitler, hedef botu çözümler ve entegrasyonu açar.
5. Kullanılmış `TELEGRAM_LOGIN_CODE` secret'ını silin. Kod tek kullanımlık olsa
   da gereksiz secret bırakılmamalıdır.

Telefon ve doğrulama kodu workflow girdisi değil, yalnız GitHub Actions secret'ı
olarak kullanılmalıdır. 2FA açık hesaplar bu akış tarafından bilerek reddedilir;
onlar için doğrudan sunucu terminali kullanılmalıdır.

Kullanıcı ID tek başına giriş sağlamaz. `TELEGRAM_EXPECTED_USER_ID`, yanlış
hesap oturumunun kullanılmasını engelleyen ek kontroldür.
