import yfinance as yf

symbols = [
    "THYAO.IS","ASELS.IS","SASA.IS","KRDMD.IS","HEKTS.IS",
    "TUPRS.IS","SISE.IS","BIMAS.IS","GARAN.IS","YKBNK.IS",
    "ISCTR.IS","KOZAA.IS","KOZAL.IS","SAHOL.IS"
]

def fetch_bist_data():
    result = []
    data = yf.download(tickers=symbols, period="1d", interval="1m")
    for sym in symbols:
        try:
            close_price = data['Close'][sym].iloc[-1]
        except:
            close_price = None
        result.append({
            "symbol": sym,
            "asenax": close_price,
            "yfinance": close_price,
            "delay": None
        })
    return result
