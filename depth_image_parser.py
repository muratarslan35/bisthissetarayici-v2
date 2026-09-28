"""Strict OCR parser for the fixed Borsa Bilgi Bot depth-card layout."""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile

from PIL import Image, ImageEnhance, ImageFilter


CANVAS = (1536, 1024)
TESSERACT = os.getenv("EXTERNAL_VERIFY_TESSERACT", "tesseract")


def _ocr(image, box, psm=6, whitelist=None):
    crop = image.crop(box)
    crop = crop.resize((crop.width * 2, crop.height * 2))
    crop = ImageEnhance.Contrast(crop).enhance(1.4)
    crop = crop.filter(ImageFilter.SHARPEN)
    with tempfile.TemporaryDirectory(prefix="bist-depth-ocr-") as folder:
        source = os.path.join(folder, "region.png")
        crop.save(source)
        command = [TESSERACT, source, "stdout", "--psm", str(psm), "-l", "eng"]
        if whitelist:
            command.extend(["-c", f"tessedit_char_whitelist={whitelist}"])
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=12, check=False
        )
        if result.returncode != 0:
            raise RuntimeError(f"tesseract failed: {result.stderr.strip()[:200]}")
        return result.stdout.strip()


def _decimal(value):
    cleaned = re.sub(r"[^0-9,.-]", "", str(value or ""))
    if not cleaned:
        return None
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    elif "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _integer(value):
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    return int(digits) if digits else None


def _single_number(image, box, integer=False):
    text = _ocr(image, box, psm=7, whitelist="0123456789.,-%")
    return _integer(text) if integer else _decimal(text)


def _parse_header(image):
    title = _ocr(image, (0, 0, 650, 64), psm=7)
    match = re.search(
        r"DERINL[Iİ]K\s+([A-Z0-9]+)\s+([0-9]+(?:[.,][0-9]+)?)", title, re.I
    )
    symbol = match.group(1).upper() if match else None
    price = _decimal(match.group(2)) if match else None
    change_text = _ocr(image, (400, 0, 570, 64), psm=7, whitelist="0123456789.,-%+")
    return {
        "symbol": symbol,
        "last_price": price,
        "change_pct": _decimal(change_text),
        "previous_close": _single_number(image, (0, 100, 252, 145)),
        "high": _single_number(image, (252, 100, 510, 145)),
        "low": _single_number(image, (510, 100, 765, 145)),
        "limit_up": _single_number(image, (765, 100, 1025, 145)),
        "limit_down": _single_number(image, (1025, 100, 1280, 145)),
        "volume": _single_number(image, (1280, 100, 1536, 145), integer=True),
    }


def _parse_depth(image, current_price):
    text = _ocr(image, (0, 150, 765, 725), psm=6)
    levels = []
    raw_levels = []
    anomalies = []
    totals = {}
    for line in text.splitlines():
        tokens = re.findall(r"\d+(?:[.,]\d+)?", line)
        if len(tokens) == 6:
            buy_orders = _integer(tokens[0])
            buy_qty = _integer(tokens[1])
            bid = _decimal(tokens[2])
            ask = _decimal(tokens[3])
            sell_qty = _integer(tokens[4])
            sell_orders = _integer(tokens[5])
            if not all(v is not None for v in (buy_orders, buy_qty, bid, ask, sell_qty, sell_orders)):
                continue
            raw_level = {
                "buy_orders": buy_orders, "buy_quantity": buy_qty, "bid": bid,
                "ask": ask, "sell_quantity": sell_qty, "sell_orders": sell_orders,
            }
            raw_levels.append(raw_level)
            # Preserve but quarantine visibly corrupt/OCR-shifted price rows
            # such as 13.46 when the stock trades at 2.88.
            if current_price and not (
                current_price * 0.75 <= bid <= current_price * 1.25
                and current_price * 0.75 <= ask <= current_price * 1.25
            ):
                anomalies.append({**raw_level, "reason": "price_outside_25pct_band"})
                continue
            levels.append(raw_level)
        elif len(tokens) == 5 and levels:
            totals = {
                "buy_orders": _integer(tokens[0]),
                "buy_quantity": _integer(tokens[1]),
                "mid_price": _decimal(tokens[2]),
                "sell_quantity": _integer(tokens[3]),
                "sell_orders": _integer(tokens[4]),
            }
    return levels, raw_levels, anomalies, totals, text


def _parse_history(image):
    text = _ocr(image, (770, 150, 1536, 1024), psm=6)
    trades = []
    pattern = re.compile(
        r"^(\d{2}:\d{2}:\d{2})\s+([0-9]+(?:[.,][0-9]+)?)[^\s]*\s+"
        r"([0-9][0-9.]*)\s+(.+?)\s+([^\s]+)$"
    )
    for line in text.splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        trades.append({
            "time": match.group(1), "price": _decimal(match.group(2)),
            "quantity": _integer(match.group(3)), "buyer": match.group(4).strip(),
            "seller": match.group(5).strip(),
        })
    return trades, text


def _institution_flow(trades):
    flows = {}
    for trade in trades:
        quantity = int(trade.get("quantity") or 0)
        buyer = trade.get("buyer") or "UNKNOWN"
        seller = trade.get("seller") or "UNKNOWN"
        flows.setdefault(buyer, {"buy": 0, "sell": 0})["buy"] += quantity
        flows.setdefault(seller, {"buy": 0, "sell": 0})["sell"] += quantity
    result = []
    for institution, values in flows.items():
        result.append({
            "institution": institution, "buy_quantity": values["buy"],
            "sell_quantity": values["sell"],
            "net_quantity": values["buy"] - values["sell"],
        })
    return sorted(result, key=lambda x: abs(x["net_quantity"]), reverse=True)


def _parse_theoretical(image):
    return {
        "previous_close": _single_number(image, (0, 790, 382, 866)),
        "theoretical_price": _single_number(image, (382, 790, 765, 866)),
        "theoretical_difference_pct": _single_number(image, (0, 866, 382, 944)),
        "theoretical_quantity": _single_number(image, (382, 866, 765, 944), integer=True),
        "limit_down": _single_number(image, (0, 944, 382, 1024)),
        "limit_up": _single_number(image, (382, 944, 765, 1024)),
    }


def parse_depth_image(image_bytes, expected_symbol=None):
    if not shutil.which(TESSERACT):
        raise RuntimeError("tesseract executable is not installed")
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB").resize(CANVAS)
    header = _parse_header(image)
    levels, raw_levels, anomalies, totals, depth_ocr = _parse_depth(
        image, header.get("last_price")
    )
    trades, history_ocr = _parse_history(image)
    theoretical = _parse_theoretical(image)

    if totals.get("mid_price") and header.get("last_price"):
        # The lira glyph is occasionally OCR'd as a trailing 6 (2.88₺ ->
        # 2.886). The independently read depth footer is the safer value.
        if abs(totals["mid_price"] - header["last_price"]) / totals["mid_price"] < 0.01:
            header["last_price"] = totals["mid_price"]

    buy_qty = totals.get("buy_quantity") or sum(x["buy_quantity"] for x in levels)
    sell_qty = totals.get("sell_quantity") or sum(x["sell_quantity"] for x in levels)
    ratio = round(buy_qty / sell_qty, 4) if sell_qty else None
    expected = str(expected_symbol or "").upper().replace(".IS", "")
    symbol_ok = not expected or header.get("symbol") == expected
    # Seven reliable levels + exact totals + five prints are required. The source
    # advertises ten levels, but corrupt out-of-band rows are discarded rather
    # than silently accepted as trading evidence.
    complete = bool(
        symbol_ok and header.get("last_price") and header.get("volume") is not None
        and len(levels) >= 7 and buy_qty and sell_qty and len(trades) >= 5
    )
    return {
        "source": "borsabilgibot_image",
        "header": header,
        "depth_levels": levels,
        "raw_depth_levels": raw_levels,
        "depth_anomalies": anomalies,
        "depth_totals": {**totals, "buy_sell_ratio": ratio},
        "trade_history": trades,
        "institution_flow": _institution_flow(trades),
        "theoretical": theoretical,
        "quality": {
            "symbol_match": symbol_ok, "valid_depth_levels": len(levels),
            "raw_depth_levels": len(raw_levels), "anomaly_rows": len(anomalies),
            "trade_rows": len(trades), "complete": complete,
        },
        "confirmation": bool(complete and ratio is not None and ratio >= 1.20),
        "raw_ocr": {"depth": depth_ocr, "history": history_ocr},
    }
