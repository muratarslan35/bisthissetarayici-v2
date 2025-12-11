# utils.py
import math
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

FALLBACK_SYMBOLS = [
"ADESE.IS","ADEL.IS","AEFES.IS","AGHOL.IS","AGLYO.IS","AHGAZ.IS","AHSKY.IS","AKBNK.IS","AKENR.IS",
"AKGRT.IS","AKSA.IS","AKSEN.IS","ALARK.IS","ALCTL.IS","ALFAS.IS","ALGN.IS","ALKIM.IS","ALMAD.IS",
"ANELE.IS","ARDYZ.IS","ARMDA.IS","ARTI.IS","ASELS.IS","ASUZU.IS","ATEKS.IS","ATPET.IS",
"ATLAS.IS","ATSYH.IS","ATTP.IS","AVGYO.IS","AVHOL.IS","AVOD.IS","AYCES.IS","AYDEM.IS","AYEN.IS",
"BALSU.IS","BERA.IS","BIMAS.IS","BLCYT.IS","BOBET.IS","BRKSN.IS","BRYAT.IS","BSRN.IS","BTCIM.IS","BURCE.IS",
"CANTE.IS","CCOLA.IS","CEMAS.IS","CEMTS.IS","CGLYO.IS","CMENT.IS","CIMSA.IS","CLEBI.IS","COMDO.IS",
"CUSAN.IS","DAGHL.IS","DENGE.IS","DERIM.IS","DESA.IS","DEVA.IS","DGNMO.IS","DIRIT.IS","DITAS.IS",
"DZGYO.IS","EGEEN.IS","EGGUB.IS","EGPRO.IS","EKGYO.IS","EMKEL.IS","ENKAI.IS","ENJSA.IS","ERCB.IS",
"EREGL.IS","ERSU.IS","EUREN.IS","FROTO.IS","FFKRL.IS","FMIZP.IS","FONET.IS","GARAN.IS","GEDZA.IS",
"GENIL.IS","GEREL.IS","GLBMD.IS","GLRYH.IS","GOZDE.IS","GRSAN.IS","GUBRF.IS","GZNMI.IS","HALKB.IS",
"HEKTS.IS","HRKLB.IS","IHLGM.IS","IHGZT.IS","INDES.IS","INVEO.IS","ISATR.IS","ISBTR.IS","ISCTR.IS",
"ISFIN.IS","ISGYO.IS","ISKPL.IS","ISMEN.IS","ITTFH.IS","IZMDC.IS","JANTS.IS","KAPLM.IS","KARMA.IS",
"KARSN.IS","KATMR.IS","KENT.IS","KERVT.IS","KIMMR.IS","KLGYO.IS","KLMSN.IS","KNFRT.IS","KONTR.IS",
"KONYA.IS","KORDS.IS","KOTON.IS","KOZAA.IS","KOZAL.IS","KRDMA.IS","KRDMB.IS","KRDMD.IS","KRGYO.IS",
"KRONT.IS","LIDER.IS","LINK.IS","LOGO.IS","LPCIP.IS","LUKSK.IS","MAGEN.IS","MAKIM.IS","MAVI.IS",
"MAALT.IS","MARTI.IS","MEPET.IS","MGROS.IS","MIATK.IS","MPARK.IS","MTRKS.IS","NETAS.IS","NIBAS.IS",
"ODAS.IS","OYAYO.IS","OTKAR.IS","OYLUM.IS","OZBAL.IS","PAMEL.IS","PANEL.IS","PARSN.IS","PEGAS.IS",
"PEKGY.IS","PETKM.IS","PETUN.IS","PGSUS.IS","PKART.IS","PKENT.IS","POLTK.IS","PRKAB.IS","PRZMA.IS",
"PSDTC.IS","QNBFL.IS","QUAGR.IS","RAYSG.IS","RODRG.IS","RTALB.IS","RYGYO.IS","SAFKR.IS","SANEL.IS",
"SASA.IS","SARKY.IS","SAHOL.IS","SDTTR.IS","SEKUR.IS","SELVA.IS","SEGYO.IS","SELEC.IS","SISE.IS",
"SILVR.IS","SKBNK.IS","SMART.IS","SMBYO.IS","SNICA.IS","SOKE.IS","SOKM.IS","SOMA.IS","SUNTK.IS",
"SUWEN.IS","SYHGYO.IS","TATGD.IS","TAVHL.IS","TCELL.IS","TDGYO.IS","TEHOL.IS","TEPLO.IS","THYAO.IS",
"TKFEN.IS","TKNSA.IS","TLMAN.IS","TMSN.IS","TMTAS.IS","TOASO.IS","TRCAS.IS","TRGYO.IS","TRILC.IS",
"TSGBD.IS","TSGYO.IS","TSKB.IS","TSPOR.IS","TTRAK.IS","TUKAS.IS","TUPRS.IS","TUREX.IS","ULAS.IS",
"ULKER.IS","UNLU.IS","USAK.IS","UZERB.IS","VAKBN.IS","VBTYZ.IS","VERUS.IS","VKING.IS","VESBE.IS",
"VESPA.IS","VESTL.IS","YEOTK.IS","YGGYO.IS","YKBNK.IS","YONGA.IS","YUNSA.IS","YYAPI.IS","ZEDUR.IS","ZOREN.IS"
]

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period, min_periods=1).mean()
    avg_loss = loss.rolling(window=period, min_periods=1).mean().replace(0, np.nan)
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def moving_averages(df, windows=[20,50,100,200]):
    mas = {}
    for w in windows:
        if "Close" in df.columns:
            mas[w] = df["Close"].rolling(window=w, min_periods=1).mean().iloc[-1]
        else:
            mas[w] = None
    return mas

def detect_three_peaks(close_series):
    if close_series.empty or len(close_series) < 5:
        return False
    peaks = (close_series > close_series.shift(1)) & (close_series > close_series.shift(-1))
    peak_idx = close_series[peaks].index
    if len(peak_idx) < 3:
        return False
    last_three = peak_idx[-3:]
    max_peak = close_series.loc[last_three].max()
    current_price = close_series.iloc[-1]
    return current_price > max_peak

def detect_support_resistance_break(df, lookback=20):
    """
    Basit kırılım tespiti: lookback dönemi içindeki min/max (bir önceki bar dahil edilerek) üzerinden
    son kapanışın kırıp kırmadığını kontrol eder.
    """
    if "Low" not in df.columns or "High" not in df.columns:
        return False, False
    if len(df) < 2:
        return False, False
    # previous extremes (exclude current bar)
    prev_low = df["Low"].iloc[:-1].rolling(window=lookback, min_periods=1).min().iloc[-1]
    prev_high = df["High"].iloc[:-1].rolling(window=lookback, min_periods=1).max().iloc[-1]
    current = df["Close"].iloc[-1]
    support_break = current < prev_low
    resistance_break = current > prev_high
    return support_break, resistance_break

def nearest_support_resistance_from_history(df, lookback=100):
    """
    Basit destek/direnç bul (geçmiş pivot seviye yaklaşımı):
    - lookback adet bar içinden yerel yüksek/düşük pivot'ları topla,
    - en yakın üst/alt seviyeyi döndür.
    """
    if df.empty or "Close" not in df.columns:
        return None, None
    highs = df["High"].rolling(3, center=True).max()
    lows = df["Low"].rolling(3, center=True).min()
    pivots_high = df["High"][(df["High"] == highs)]
    pivots_low = df["Low"][(df["Low"] == lows)]
    pivots_high = pivots_high.dropna()
    pivots_low = pivots_low.dropna()
    if pivots_high.empty and pivots_low.empty:
        return None, None
    current = df["Close"].iloc[-1]
    # candidate resistance = pivots_high values greater than current
    resistances = [v for v in pivots_high.values if v > current]
    supports = [v for v in pivots_low.values if v < current]
    nearest_res = min(resistances) if resistances else (max(pivots_high.values) if not pivots_high.empty else None)
    nearest_supp = max(supports) if supports else (min(pivots_low.values) if not pivots_low.empty else None)
    return nearest_supp, nearest_res

def to_tr_timezone(dt):
    """
    dt: naive UTC datetime or aware datetime
    returns: aware datetime in Europe/Istanbul timezone
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo("Europe/Istanbul"))
