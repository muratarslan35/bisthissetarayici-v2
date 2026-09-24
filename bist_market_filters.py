import requests
import re
from datetime import datetime
from dateutil import parser
import json
import os
import io

# ======================================================
# 🔥 GEMINI AI (AYNI - DOKUNULMADI)
# ======================================================

from google import genai
from kap_service import get_recent_verified_events
from utils import FALLBACK_SYMBOLS

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

client = None

if GEMINI_API_KEY:
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        print("✅ Gemini aktif")
    except Exception as e:
        print("❌ Gemini init error:", e)


def extract_json_safe(text):
    try:
        if not text:
            return []

        text = text.replace("```json", "").replace("```", "").strip()
        match = re.search(r"\[.*\]", text, re.DOTALL)

        if match:
            return json.loads(match.group(0))

        return []
    except:
        return []


def deterministic_parse_brut(text):
    """
    Deterministic first-pass parser for official KAP restriction text.
    AI is never the only source of a brüt-takas decision.
    """
    if not text:
        return {}

    normalized = text.lower().replace("ı", "i")
    if "brüt" not in text.lower() and "brut" not in normalized:
        return {}

    allowed = {
        str(symbol).upper().replace(".IS", "")
        for symbol in FALLBACK_SYMBOLS
    }
    tokens = set(re.findall(r"(?<![A-Z0-9])[A-Z0-9]{2,8}(?![A-Z0-9])", text.upper()))
    symbols = sorted(token for token in tokens if token in allowed)

    dates = re.findall(r"\b(\d{2}[./-]\d{2}[./-]\d{4})\b", text)
    dates = [d.replace("/", ".").replace("-", ".") for d in dates]

    start_date = dates[0] if dates else "-"
    end_date = dates[1] if len(dates) > 1 else "-"

    results = {}
    for symbol in symbols:
        end_dt = parse_date_safe(end_date) if end_date != "-" else None
        results[f"{symbol}.IS"] = {
            "start_date": start_date,
            "end_date": end_date,
            "days_left": days_left_calc(end_dt),
            "type": "Brüt Takas (KAP - Deterministic)",
            "priority": 95,
        }

    return results


def gemini_parse_brut(text):

    try:
        if not client:
            return {}

        text = text[:12000]

        prompt = f"""
Metinden SADECE brüt takas hisselerini çıkar.

Kurallar:
- Sadece hisse kodu
- Tahmin yok
- Emin değilsen alma
- JSON dışında yazma

[
  {{"symbol":"XXX"}}
]

Metin:
{text}
"""

        response = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=prompt
        )

        data = extract_json_safe(response.text)

        results = {}

        for item in data:
            sym = item.get("symbol")

            if not sym:
                continue

            sym = sym.upper().replace(".IS", "") + ".IS"

            results[sym] = {
                "start_date": "-",
                "end_date": "-",
                "days_left": None,
                "type": "Brüt Takas (AI)",
                "priority": 80
            }

        return results

    except Exception as e:
        print("GEMINI ERROR:", e)
        return {}

# ======================================================
# CONFIG (AYNI)
# ======================================================

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "tr-TR,tr;q=0.9"
}

CACHE_TTL = 300

BRUT_CACHE = {}
LAST_UPDATE = None

MANUAL_FILE = "data/manual_brut.json"

# ======================================================
# HELPERS (AYNI)
# ======================================================

def normalize_symbol(code):
    try:
        return code.strip().upper().replace(".IS", "") + ".IS"
    except:
        return None


def parse_date_safe(date_str):
    try:
        if not date_str:
            return None
        return parser.parse(date_str, dayfirst=True).date()
    except:
        return None


def days_left_calc(dt):
    try:
        if not dt:
            return None
        return (dt - datetime.now().date()).days
    except:
        return None

# ======================================================
# MANUAL (AYNI)
# ======================================================

def load_manual():
    if not os.path.exists(MANUAL_FILE):
        return {}
    try:
        with open(MANUAL_FILE, "r") as f:
            return json.load(f)
    except:
        return {}


def merge_manual(data):

    manual = load_manual()

    for k, v in manual.items():
        k = normalize_symbol(k)
        v["priority"] = 999
        data[k] = v

    return data


def clean_expired(data):

    today = datetime.now().date()
    cleaned = {}

    for sym, d in data.items():

        end = d.get("end_date")

        if not end or end == "-":
            cleaned[sym] = d
            continue

        try:
            end_dt = datetime.strptime(end, "%d.%m.%Y").date()

            if end_dt >= today:
                d["days_left"] = (end_dt - today).days
                cleaned[sym] = d

        except:
            cleaned[sym] = d

    return cleaned

# ======================================================
# 🔥 YENİ BRÜT ENGINE (SADECE BURASI DEĞİŞTİ)
# ======================================================

def fetch_kap_html():
    """
    Read only KAP disclosure links already verified by kap_service.
    This avoids a second independent RSS reader.
    """
    results = {}

    try:
        events = get_recent_verified_events(hours=96, contains="brüt", limit=40)

        for event in events:
            try:
                link = event.get("link")
                if not link:
                    continue

                r = requests.get(link, headers=HEADERS, timeout=8)
                if r.status_code != 200:
                    continue

                text = r.text
                if "brüt" not in text.lower() and "brut" not in text.lower():
                    # Public detail HTML may be client-rendered; use the already
                    # verified KAP title/summary as a deterministic fallback.
                    text = (
                        f"{event.get('subject','')} "
                        f"{event.get('summary','')} "
                        f"{event.get('company_title','')}"
                    )

                # Restriction decisions are deterministic. AI may enrich
                # explanations, but cannot create a brüt-takas flag.
                deterministic = deterministic_parse_brut(text)
                results.update(deterministic)

            except Exception as e:
                print("KAP BRUT DETAIL ERROR:", e)

    except Exception as e:
        print("KAP BRUT VERIFIED EVENT ERROR:", e)

    return results

def fetch_doviz_html():
    results = {}

    try:
        r = requests.get("https://m.doviz.com/haber/borsa-haberleri/", headers=HEADERS, timeout=10)

        links = re.findall(r'href="(/haber/[^"]+)"', r.text)

        for link in set(links)[:10]:
            try:
                url = "https://m.doviz.com" + link
                hr = requests.get(url, headers=HEADERS, timeout=8)

                if "brüt" not in hr.text.lower():
                    continue

                results.update(gemini_parse_brut(hr.text))

            except:
                continue
    except:
        pass

    return results


def fetch_kap_pdf():
    """
    PDF fallback only for KAP IDs already verified by the central KAP service.
    """
    try:
        events = get_recent_verified_events(hours=96, contains="brüt", limit=20)

        for event in events:
            kap_id = event.get("kap_id")
            if not kap_id:
                continue

            pdf_url = f"https://www.kap.org.tr/tr/BildirimPdf/{kap_id}"
            r = requests.get(pdf_url, headers=HEADERS, timeout=8)

            if r.status_code == 200 and "application/pdf" in r.headers.get("content-type", ""):
                return r.content

    except Exception as e:
        print("KAP PDF ERROR:", e)

    return None

def parse_pdf(pdf_bytes):
    try:
        import pdfplumber

        text = ""

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text += t + "\n"

        return text

    except:
        return ""


def fetch_pdf_brut():
    results = {}

    pdf_bytes = fetch_kap_pdf()

    if not pdf_bytes:
        return results

    text = parse_pdf(pdf_bytes)

    if "brüt" not in text.lower():
        return results

    results.update(deterministic_parse_brut(text))
    return results


def merge_all(*sources):

    final = {}

    for source in sources:
        for k, v in source.items():

            if k not in final:
                final[k] = v
            else:
                if v.get("priority", 0) > final[k].get("priority", 0):
                    final[k] = v

    return final

# ======================================================
# 🔥 MAIN ENGINE (SADECE BURASI GÜNCEL)
# ======================================================

def get_brut_list():

    global BRUT_CACHE, LAST_UPDATE

    now = datetime.now()

    if LAST_UPDATE and (now - LAST_UPDATE).seconds < CACHE_TTL:
        return BRUT_CACHE

    print("🔍 BRÜT TARANIYOR...")

    kap = fetch_kap_html()
    pdf = fetch_pdf_brut()

    # Automatic restriction state comes only from verified official KAP
    # disclosures. Third-party pages and AI-only extraction are excluded.
    final = merge_all(kap, pdf)

    final = merge_manual(final)
    final = clean_expired(final)

    if not final:
        print("❌ BRÜT YOK → MANUAL")
        final = merge_manual({})

    BRUT_CACHE = final
    LAST_UPDATE = now

    print(f"🔥 FINAL BRÜT: {len(final)}")

    return final

# ======================================================
# 🔥 HALT (AYNI)
# ======================================================

def detect_halt_from_data(item):

    try:

        tf15 = item.get("tf", {}).get("15m")
        if not tf15:
            return False

        df = tf15.get("df")

        if df is None or len(df) < 5:
            return False

        last = df.iloc[-1]
        prev = df.iloc[-2]

        if last["Close"] == prev["Close"]:

            vol_ma = df["Volume"].rolling(20).mean().iloc[-1]

            if vol_ma and last["Volume"] < vol_ma * 0.05:
                return True

        return False

    except:
        return False


LAST_HALT_CACHE = set()
LAST_HALT_UPDATE = None
HALT_CACHE_TTL = 30


def get_halt_list(data=None):

    global LAST_HALT_CACHE, LAST_HALT_UPDATE

    now = datetime.now()

    if LAST_HALT_UPDATE and (now - LAST_HALT_UPDATE).seconds < HALT_CACHE_TTL:
        return LAST_HALT_CACHE

    halt_set = set()

    if not data:
        return halt_set

    try:

        for item in data:

            symbol = item.get("symbol")

            if not symbol:
                continue

            if detect_halt_from_data(item):
                halt_set.add(symbol)

    except Exception as e:
        print("HALT scan error:", e)

    LAST_HALT_CACHE = halt_set
    LAST_HALT_UPDATE = now

    print(f"⛔ HALT SAYISI: {len(halt_set)}")

    return halt_set

# ======================================================
# 🚀 MOMENTUM (AYNI)
# ======================================================

def validate_tradeable_momentum(df, price):

    try:

        low_30 = df["Low"].rolling(30).min().iloc[-1]
        move_pct = (price - low_30) / low_30

        if move_pct > 0.05:
            return False, "GEÇ KALINMIŞ"

        last5 = df["Close"].iloc[-5:]
        move5 = (last5.iloc[-1] - last5.iloc[0]) / last5.iloc[0]

        if move5 > 0.03:
            return False, "PARABOLİK (TEHLİKELİ)"

        if move_pct < 0.02:
            return True, "ERKEN"
        elif move_pct < 0.04:
            return True, "ORTA"
        else:
            return True, "GEÇ"

    except:
        return False, None

# ======================================================
# 🚀 PULLBACK (AYNI)
# ======================================================

def detect_pullback_entry(df, price):

    try:

        if df is None or len(df) < 25:
            return False, None

        low = df["Low"].iloc[-20:-10].min()
        high = df["High"].iloc[-10:-3].max()

        move_pct = (high - low) / low

        if move_pct < 0.025:
            return False, None

        last_high = df["High"].iloc[-3]
        pullback_pct = (last_high - price) / last_high

        if pullback_pct < 0.005:
            return False, None

        if pullback_pct > 0.03:
            return False, None

        ema20 = df["Close"].ewm(span=20).mean().iloc[-1]

        if price < ema20:
            return False, None

        last = df.iloc[-1]
        prev = df.iloc[-2]

        if not (last["Close"] > last["Open"] and last["Close"] > prev["Close"]):
            return False, None

        vol_ma = df["Volume"].rolling(20).mean().iloc[-1]

        if last["Volume"] < vol_ma:
            return False, None

        return True, round(pullback_pct * 100, 2)

    except:
        return False, None
