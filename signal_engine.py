# signal_engine.py
from utils import tr_now, should_send
import requests
import json
import datetime

TELEGRAM_TOKEN = "<TOKENIN>"
CHAT_ID = "<CHAT_ID>"

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": msg})

def check_signals(data):
    triggered = []

    for d in data:
        sym = d["symbol"]

        # RSI
        if d["RSI"] < 20 and should_send(sym, "rsi_low"):
            triggered.append((sym, "RSI < 20"))
        if d["RSI"] > 80 and should_send(sym, "rsi_high"):
            triggered.append((sym, "RSI > 80"))

        # Destek kırılım
        if d["support_break"] and should_send(sym, "support_break"):
            triggered.append((sym, "Destek Kırılımı"))

        # Direnç kırılım
        if d["resistance_break"] and should_send(sym, "resistance_break"):
            triggered.append((sym, "Direnç Kırılımı"))

        # 3 tepe
        if d["three_peak_break"] and should_send(sym, "three_peak"):
            triggered.append((sym, "Üç Tepe Formasyonu Kırıldı"))

        # 11 yeşil
        if d["green_mum_11"] and should_send(sym, "green11"):
            triggered.append((sym, "11:00 Yeşil Mum"))

        # 15 yeşil
        if d["green_mum_15"] and should_send(sym, "green15"):
            triggered.append((sym, "15:00 Yeşil Mum"))

    # Telegram gönder
    for sym, text in triggered:
        send_telegram(f"🟢 {sym} — {text}\nSinyal zamanı (TR): {tr_now()}")

    return triggered
