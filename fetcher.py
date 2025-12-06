import requests
import yfinance as yf
import time

ASENAX_URL = "https://asenax.com/api/v2/bist"
symbols = [
    "THYAO.IS","ASELS.IS","SASA.IS","KRDMD.IS","HEKTS.IS",
    "TUPRS.IS","SISE.IS","BIMAS.IS","GARAN.IS","YKBNK.IS",
    "ISCTR.IS","KOZAA.IS","KOZAL.IS","SAHOL.IS"
]

def fetch_bist_data():
    result = []
    asenax = requests.get(ASENAX_URL).json()

    for sym in symbols:
        yf_data = yf.download(sym, period="1d", interval="1m")
        
        if yf_data.empty:
            price_yf = None
        else:
            price_yf = float(yf_data["Close"].iloc[-1])

        if sym.replace(".IS","") in asenax:
            price_asx = float(asenax[sym.replace(".IS","")]["last"])
        else:
            price_asx = None

        delay_flag = None
        if price_asx and price_yf:
            diff = abs(price_asx - price_yf)
            if diff > price_asx * 0.002:  # %0.2 fark test
                delay_flag = "DELAYED"

        result.append({
            "symbol": sym,
            "asenax": price_asx,
            "yfinance": price_yf,
            "delay": delay_flag
        })

    return result
