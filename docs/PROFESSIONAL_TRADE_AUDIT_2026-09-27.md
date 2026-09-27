# BIST Trading V5 — Profesyonel Trade ve Quant Denetimi

**Tarih:** 27 Eylül 2026  
**İncelenen ana sürüm:** `23709fc5bd51766275e35c220a6223dac0438383`  
**Kapsam:** Yalnızca `muratarslan35/bisthissetarayici-v2`. IMS projesi kapsam dışıdır.

> Bu belge yatırım tavsiyesi değildir. Amaç; sinyal motorunun teknik, istatistiksel ve operasyonel güvenilirliğini artırmaktır.

## 1. Yönetici özeti

Proje artık basit bir RSI/EMA tarayıcısı değildir. Gerçek mumlardan çoklu zaman dilimi üreten, BIST evrenini dinamik izleyen, KAP olaylarını doğrulayan, pozisyon ve gün içi sinyalleri ayıran, sinyal yaşam döngüsünü saklayan ve ayrı worker/web servisleriyle çalışan ileri seviye bir **canlı karar destek sistemi** haline gelmiştir.

Ancak bugün itibarıyla proje, **profesyonel bir sinyal üretim platformu** seviyesinde olsa da henüz **kanıtlanmış quant trading sistemi** seviyesinde değildir. Eksik halka daha fazla indikatör değil; point-in-time veri, yürütme maliyetleri, event-driven backtest, walk-forward/out-of-sample test, olasılık kalibrasyonu ve istatistiksel anlamlılık ölçümüdür.

En önemli sonuç:

- `%100 başarılı sinyal` finansal piyasalarda mümkün ve dürüstçe vaat edilebilir bir hedef değildir.
- Gerçek hedef; maliyet sonrası pozitif beklenti, kontrollü maksimum düşüş, rejimler arasında dayanıklılık ve canlı sonuçların backtest aralığında kalmasıdır.
- Son 4H gevşetmesi daha erken pozisyon sinyali yakalayabilir; fakat test edilmeden canlı kalite artışı kabul edilmemelidir.

## 2. Mevcut seviye

| Alan | Seviye | Değerlendirme |
|---|---:|---|
| Canlı sinyal mimarisi | 8.5/10 | Position/intraday ayrımı, stateful yayın, hızlı hat ve KAP doğrulaması güçlü |
| Operasyon ve deploy | 8.5/10 | Ayrı servisler, atomik release, CI, kaynak izolasyonu ve canlı audit iyi |
| Veri kalitesi | 6/10 | Batch/cache/backoff iyi; ücretsiz kaynak, gecikme ve resmî tick/order-book eksikliği sınırlayıcı |
| Risk modelleme | 6.5/10 | ATR/yapı stopları ve MFE/MAE mevcut; portföy ve korelasyon riski eksik |
| Backtest gerçekçiliği | 3/10 | Üretim stratejilerinin event-driven, maliyetli, point-in-time replay motoru yok |
| İstatistiksel doğrulama | 2.5/10 | Walk-forward, embargo, DSR/PBO, bootstrap güven aralığı ve kalibrasyon yok |
| Gerçek işlem yürütmesi | 3/10 | Bid/ask, tick, limit, kısmi dolum, kuyruk ve market-impact modeli yok |

**Genel sonuç:** Açık kaynak BIST sinyal botlarının çoğundan daha kapsamlı ve üretime daha yakın; kurumsal quant standardına geçişte ana açık ölçüm ve doğrulama katmanıdır.

## 3. Son değişikliklerin trade açısından değerlendirilmesi

### 3.1 Güçlü ve doğru değişiklikler

1. **Position ve intraday veri güveninin ayrılması**  
   15 dakikalık veri gecikince günlük/4H yapının tamamen geçersiz sayılmaması doğru bir tasarımdır. İki ufkun veri SLA'ları farklı olmalıdır.

2. **Gerçek mumlu, stateful sinyal kartları**  
   Sinyalin ilk oluşumu, güçlenmesi ve yaşam döngüsünün ayrılması; tekrarlı Telegram mesajından daha profesyoneldir. Paper ledger ile birlikte denetlenebilirlik sağlar.

3. **Piyasa geneli sabit kota yerine kalite eşiği**  
   Güçlü bir fırsat sırf günlük kota doldu diye gizlenmemelidir. Spam kontrolünün sembol durum geçişine taşınması mantıklıdır.

4. **Hızlı mover hattı**  
   Geniş evreni her 20 saniyede ağır mum verisiyle taramak yerine adayları hızlı quote katmanına yükseltmek kaynak verimliliği sağlar.

5. **Gecikmiş giriş filtresi**  
   Günlük harekette tavana yaklaşan hisseleri yeni girişten çıkarmak BIST fiyat limitleri açısından gereklidir.

6. **Atomik, sürümlü sanal ortam deploy'u**  
   Trade mantığından bağımsız görünse de sinyal sürekliliği ve geri dönüş güvenliği için kurumsal seviyede doğru adımdır.

### 3.2 Dikkat gerektiren son değişiklik: 4H veto yerine puan

`39ef043` ile 4H trend koşulu ikili veto olmaktan çıkarılıp puana dönüştürüldü. Bu, trend başlangıcını daha erken yakalamak için makul bir hipotezdir; fakat mevcut ağırlıklar ampirik olarak öğrenilmemiştir:

- Fiyatın 4H EMA20 üzerinde olması: `+4`
- EMA20 eğiminin pozitif olması: `+4`
- Son 3 bara göre güçlenme: `+3`
- EMA20'nin EMA50'nin %95'ine yaklaşması: `+2`
- RSI(4H) >= 50: `+3`
- Kabul eşiği: `>=5`

Bu yapıda tek bir güçlü koşul ile zayıf birkaç koşul kolayca eşiği geçebilir. Ayrıca aynı trend bilgisini ölçen korelasyonlu özellikler ayrı kanıtlar gibi toplanmaktadır. Sonuç olarak:

- **Artı:** geç kalan EMA20/EMA50 kesişimini beklemeden erken sinyal üretir.
- **Eksi:** yatay piyasada yalancı dönüşleri ve whipsaw sayısını artırabilir.
- **Karar:** değişiklik geri alınmamalı; `ADAPTIVE_V5` challenger olarak ölçülmeli. Eski politika champion olarak aynı veri üzerinde paralel çalıştırılmalıdır.

### 3.3 Hızlı hat için kritik risk

TradingView hızlı quote katmanı 15/30/60 saniyelik hareketi yalnız uygulama açıldıktan sonra RAM'de biriken kısa geçmişten çıkarıyor. Restart sonrası geçmiş sıfırlanır; ilk örnekler gerçek 60 saniyelik pencereyi temsil etmeyebilir. Bu ölçümler sinyal girdisi olacaksa:

- minimum warm-up süresi zorunlu olmalı,
- örnek sayısı ve gerçek pencere uzunluğu sinyale yazılmalı,
- restart sonrasında warm-up bitene kadar aday üretilmemeli,
- mümkünse geçmiş kalıcı, zaman damgalı ring-buffer'a alınmalıdır.

## 4. Kritik eksikler

### P0 — Sinyal sonucunu doğru ölçmeyi engelleyenler

1. **Üretim stratejisi için event-driven replay/backtest yok**  
   Testler fonksiyon davranışını doğruluyor; stratejinin para kazandırdığını doğrulamıyor.

2. **Paper ledger fill modeli gerçekçi değil**  
   Giriş sinyal anındaki son fiyat kabul ediliyor. Spread, gecikme, emir tipi, tick yuvarlama, kısmi dolum ve likidite yok.

3. **OHLC sıralama belirsizliği çözülmüyor**  
   Aynı mum içinde stop ve hedef görülürse hangisinin önce olduğu bilinemez. Daha düşük zaman dilimi veya muhafazakâr “stop önce” politikası gerekir.

4. **MFE/MAE yalnız gözlenen son fiyatlarla güncelleniyor**  
   Mumun high/low değerleri kullanılmadığında gerçek adverse/favorable excursion kaçabilir.

5. **Point-in-time BIST evreni yok**  
   Bugünkü sembolleri geçmişe uygulamak survivorship ve selection bias üretir. Tarihte o gün işlem gören, endekste olan/olmayan ve sonradan kapanan hisseler saklanmalıdır.

6. **Kurumsal aksiyon düzeltmeleri doğrulanmıyor**  
   Bedelli/bedelsiz, bölünme, temettü ve sembol değişikliği sinyal ve getiri serisini bozabilir.

### P1 — Quant doğrulama eksikleri

- Anchored ve rolling walk-forward test
- Purged/embargo edilmiş zaman serisi validasyonu
- Train/validation/test dönemlerinin kesin ayrılması
- Parametre araması sayısının kaydı
- Deflated Sharpe Ratio ve Probability of Backtest Overfitting
- Bootstrap ile expectancy, hit-rate ve drawdown güven aralıkları
- Rejim bazlı sonuç: risk-on, risk-off, yatay, yüksek/düşük volatilite
- Likidite dilimi ve günün saati bazlı performans
- Strategy × score-band × regime kalibrasyonu

### P1 — Portföy riski

Mevcut sistem tek sinyal riskini yönetiyor; eşzamanlı portföy riskini yönetmiyor. Eklenmesi gerekenler:

- İşlem başına sabit risk bütçesi: ör. sermayenin `%0.25–0.50`si
- Günlük maksimum kayıp ve kill-switch
- Sektör yoğunlaşma limiti
- Korelasyon kümesi limiti
- Aynı piyasa hareketine bağlı sinyallerde toplam beta/volatilite limiti
- Likiditeye göre maksimum pozisyon: ADV'nin küçük bir yüzdesi
- Volatility targeting ve drawdown sonrası otomatik risk azaltma

### P2 — Veri ve yürütme

- Ücretsiz Yahoo verisi araştırma ve gözetim için kullanılabilir; gerçek zaman garantili profesyonel execution feed değildir.
- TradingView scanner katmanı hızlı aday keşfi için yararlı olsa da resmî sözleşmeli market-data yerine geçmez.
- Bid/ask, order-book imbalance, gerçek trade aggressor ve queue position olmadan “mikroyapı sinyali” iddia edilmemelidir.
- BIST seansları, açılış/kapanış müzayedeleri, devre kesici, fiyat adımı ve fiyat marjı simüle edilmelidir.

## 5. GitHub BIST projeleriyle karşılaştırma

| Proje | Güçlü tarafı | Bu projeye göre durum |
|---|---|---|
| `Safa675/bist-quant` | Faktör araştırması, walk-forward, işlem maliyeti/slippage modelleri, IC/bootstrap, portföy motoru | Quant araştırma ve doğrulamada daha ileri; canlı Telegram/KAP/operasyon hattında bu proje daha güçlü |
| `saidsurucu/borsapy` | Türkiye verisi için geniş kütüphane, teknik analiz, screener, replay/backtest, komisyon ve risk metrikleri | Genel amaçlı veri/analiz kütüphanesi daha olgun; ürünleşmiş canlı sinyal akışında bu proje daha odaklı |
| `omerada/bist-robogo` | FastAPI/PostgreSQL/Celery, broker/order/portfolio/backtest servis ayrımı, kapsamlı testler | Uygulama mimarisi ve broker simülasyonunda daha geniş; bu proje aktif sinyal ve KAP kullanımında daha somut |
| `sBugraTiryaki/BIST-Backtesting` | Web tabanlı çoklu strateji backtest ve temel performans metrikleri | Backtest görünürlüğü daha iyi; canlı tarama ve olay sistemi bu projede daha güçlü |
| `zeynepCankara/Stock-Price-Predictor-BIST100` | Basit tarihsel ML örneği | Eski ve dar kapsamlı; mevcut proje belirgin biçimde daha ileri |
| `Therealgba/BIST100-Mean-Reversion...` | Mean-reversion ile buy-and-hold karşılaştırması | Tek stratejili araştırma; mevcut proje kapsam olarak çok daha ileri |

### Alınması gereken fikirler

Kod kopyalamak yerine şu yetenekler uyarlanmalıdır:

- `bist-quant`: point-in-time panel, walk-forward runner, IC/quantile analizi, maliyet ve slippage katmanı
- `borsapy`: standart BacktestResult metrikleri ve replay yaklaşımı
- `bist-robogo`: order/portfolio/risk servis sınırları ve asenkron ağır test işleri
- `BIST-Backtesting`: stratejiyi buy-and-hold ve BIST100 benchmark'ı ile aynı ekranda kıyaslama

## 6. Dünyada başarı göstermiş teknikler nasıl uyarlanmalı?

“En başarılı tek teknik” yoktur. Dayanıklı sistemler genellikle düşük korelasyonlu edge'leri birleştirir.

### 6.1 Kesitsel momentum ve göreceli güç

Projede `relative_strength_percentile` zaten doğru yönde bir temel oluşturuyor. Profesyonel hale getirmek için:

- 20 gün tek başına değil; 20/60/120 günlük, son 5 günü ayrı değerlendiren bileşik momentum,
- BIST100'e ve sektör endeksine göre residual strength,
- likidite ve volatiliteye göre normalize edilmiş ranking,
- en güçlü quantile ile orta quantile arasındaki ileri getiri farkı,
- her skor dilimi için bootstrap güven aralığı.

### 6.2 Trend following + volatility scaling

EMA hizası tek başına yeterli değildir. Trend sinyalinin büyüklüğü ATR/gerçekleşen volatilite ile normalize edilmeli; pozisyon boyutu volatilite yükseldiğinde küçülmelidir. Aynı yönü ölçen EMA, slope ve RSI puanlarının çift sayımı azaltılmalıdır.

### 6.3 Volatility contraction → expansion

Momentum ignition için uygundur:

- Bollinger bandwidth / ATR percentile sıkışması,
- hacim kuruması,
- sonrasında session VWAP ve opening-range üstünde hacimli genişleme,
- girişin breakout'tan ATR cinsinden uzaklığı,
- sektör ve piyasa breadth teyidi.

Bu özellikler önce tek tek information coefficient ve forward-return bucket analiziyle ölçülmelidir.

### 6.4 Rejim koşullu ensemble

Tek puan her piyasa için kullanılmamalıdır:

- Trend/risk-on: breakout ve momentum ağırlığı
- Yatay/düşük trend: mean-reversion yalnız sıkı likidite ve destek koşuluyla
- Risk-off: daha yüksek eşik, daha küçük risk veya yeni long sinyalini durdurma
- Yüksek volatilite: daha geniş stop fakat daha küçük pozisyon

Rejim, geleceği bilen etiketle değil yalnız o anda mevcut verilerle belirlenmelidir.

### 6.5 KAP event study

KAP skoru elle belirlenmiş kalıcı puan olmamalıdır. Bildirim türü bazında şu ileri getiriler ölçülmelidir:

- 15 dk, 1 saat, seans sonu, +1 gün, +5 gün
- piyasa ve sektör düzeltilmiş abnormal return
- duyuru zamanı, likidite, gap ve ilk RVOL'a göre ayrım
- örneklem sayısı ve güven aralığı

Yeterli örnek olmayan KAP türü “kanıtlanmamış” kalmalıdır.

### 6.6 Meta-labeling ve olasılık kalibrasyonu

Ana strateji yönü seçsin; ikinci model yalnız “bu sinyal alınmalı mı?” sorusunu yanıtlasın. Model girdileri sinyal anında bilinen değerlerle sınırlı olmalıdır. Çıktı `88 puan` değil, kalibre edilmiş bir olasılık olmalıdır:

- `P(TP1 before stop | setup, regime, liquidity)`
- beklenen net R: `p_win × avg_win_R − p_loss × avg_loss_R − costs_R`
- Brier score, reliability curve ve calibration error

Gradient boosting gibi modeller kullanılabilir; LSTM/Transformer ancak point-in-time büyük veri ve güçlü basit benchmark'ları geçerse değerlendirilmelidir.

## 7. `%100 başarı` yerine profesyonel başarı standardı

Başarı oranı tek başına yanıltıcıdır. %90 kazanan ama tek kayıpta tüm kârı veren sistem başarısızdır. Üretim geçiş kapısı aşağıdaki metriklerle kurulmalıdır:

| Metrik | Minimum kabul yaklaşımı |
|---|---|
| Net expectancy | Komisyon + spread + slippage sonrası pozitif |
| Profit factor | Out-of-sample dönemde > 1; tercihen güven aralığının alt sınırı da 1'e yakın/üstünde |
| Max drawdown | Önceden tanımlı risk bütçesi içinde |
| Calibration | Tahmin edilen olasılık ile gerçekleşen oran uyumlu |
| Stability | Yıllar, rejimler, sektörler ve likidite dilimlerinde tek döneme bağımlı değil |
| Sample size | Her strateji/rejim için anlamlı örnek; az örnekte kesin hüküm yok |
| Live parity | Canlı fill ve sonuçlar backtest güven bandından taşmıyor |

Eşikler geçmişte en iyi görünen sayıya göre seçilmemeli; ekonomik/risk hedefinden önceden tanımlanmalıdır.

## 8. Önerilen uygulama planı

### Faz 0 — Ölçümü kilitle (önce)

1. Sinyal anı feature snapshot'ını değişmez olarak sakla.
2. `signal_time`, `decision_time`, `publish_time`, `first_tradable_time` alanlarını ayır.
3. Sonraki bar open veya gerçek bid/ask üzerinden muhafazakâr fill modeli kur.
4. BIST tick/fiyat limiti/devre kesici kurallarını uygula.
5. MFE/MAE'yi mum high/low ile hesapla.
6. Aynı mumda stop ve hedef varsa muhafazakâr sıralama uygula.

### Faz 1 — Replay ve walk-forward

1. Üretimdeki aynı `strategy_v3` fonksiyonlarını kullanan event-driven replay oluştur.
2. Point-in-time evren ve kurumsal aksiyonlu veri seti kur.
3. Sabit train → validation → untouched test ayrımı yap.
4. Rolling/anchored walk-forward ve purged embargo uygula.
5. Her deneyin veri sürümü, kod SHA'sı ve parametrelerini kaydet.

### Faz 2 — Champion/challenger

1. Eski 4H-veto modeli: champion.
2. Adaptif 4H-puan modeli: challenger.
3. İkisini aynı canlı veriyle shadow modda en az yeterli örnek oluşana kadar çalıştır.
4. Promotion kararını net expectancy, drawdown ve kalibrasyonla ver.

### Faz 3 — Edge geliştirme

1. RS'yi sektör-nötr ve çok ufuklu hale getir.
2. Volatility contraction/expansion özelliklerini ekle.
3. KAP event-study skorlarını veriyle öğren.
4. Meta-label ve kalibrasyon katmanı kur.
5. Korelasyonlu özelliklerde double-counting'i azalt.

### Faz 4 — Portföy ve canlı kontrol

1. Volatility-targeted position sizing.
2. Sektör/korelasyon limitleri.
3. Günlük kayıp ve drawdown kill-switch.
4. Veri gecikmesi/source divergence circuit breaker.
5. Backtest–paper–live sonuç farkı alarmı.

## 9. Hemen uygulanmaması gerekenler

- Sırf başarı oranını yükseltmek için eşikleri geçmiş sonuçlara göre sürekli değiştirmek
- Aynı trend bilgisini veren daha fazla indikatörü puana eklemek
- Küçük veriyle derin öğrenme kullanmak
- Güncel BIST100 listesini geçmişin tamamına uygulamak
- Son fiyatı her zaman gerçek fill kabul etmek
- TP1 görülmesini otomatik “kazançlı işlem” saymak
- Ücretsiz gecikmeli veriyi gerçek zaman garantili veri gibi sunmak
- Shadow test olmadan yeni puanlamayı tek üretim politikası yapmak

## 10. Denetim kanıtları

- Son ana commit için GitHub Actions `BIST V3 CI` başarıyla tamamlandı: run `36303046331`.
- Yerel bağımlılıksız test koşusunda 22 test geçti; 3 test `requests` paketi yerel audit ortamında bulunmadığı için import aşamasında çalışmadı. Bu üçü kod assertion hatası değildir. GitHub CI tam bağımlılık kurulumu ile geçti.
- İncelenen son commitler: `8794dcb`, `239cb99`, `e3f3de5`, `39ef043`, `f002ff2`, `0b0fa86`, `23709fc`.

## 11. Kaynaklar

- Borsa İstanbul, piyasa işleyişi: https://www.borsaistanbul.com/en/markets/equity-market/market-functioning
- Borsa İstanbul, fiyat marjları: https://www.borsaistanbul.com/en/price-bands
- TCMB, Borsa İstanbul gün içi desenleri ve likidite: https://www.tcmb.gov.tr/wps/wcm/connect/EN/TCMB%2BEN/Main%2BMenu/Publications/Research/Working%2BPaperss/2012/12-26
- Bailey vd., The Probability of Backtest Overfitting: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253
- Bailey vd., The Effects of Backtest Overfitting on Out-of-Sample Performance: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2308659
- Hurst, Ooi, Pedersen, A Century of Evidence on Trend-Following Investing: https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing
- Novy-Marx ve Velikov, A Taxonomy of Anomalies and Their Trading Costs: https://academic.oup.com/rfs/article/29/1/104/1844518
- BIST100 momentum araştırması: https://dergipark.org.tr/en/pub/mufad/article/402435
- BIST momentum, büyüklük ve DD/PD çalışması: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2731176

## 12. Nihai karar

Proje **korunmaya ve geliştirilmeye değer** bir temel üzerindedir. Mevcut mimari açık kaynak BIST botlarının çoğundan ileridir. Bir sonraki profesyonel sıçrama yeni sinyal eklemek değil, mevcut sinyallerin gerçek edge'ini ölçen bir **Quant Validation Layer** kurmaktır.

Önerilen ilk geliştirme paketi:

1. Event-driven replay/backtest
2. Gerçekçi fill + maliyet modeli
3. Walk-forward ve point-in-time evren
4. Champion/challenger 4H testi
5. Kalibre olasılık + expectancy dashboard'u

Bu beş madde tamamlanmadan “% başarı”, “en iyi strateji” veya otomatik gerçek emir iddiası üretim standardı olarak kabul edilmemelidir.
