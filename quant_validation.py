"""Dependency-light validation metrics for the persistent paper ledger."""

import math


VALIDATION_VERSION = "QUANT_VALIDATION_V1"


def _values(rows, key="net_result_pct"):
    out = []
    for row in rows or []:
        value = row.get(key)
        if value is None and key == "net_result_pct":
            value = row.get("result_pct")
        try:
            value = float(value)
            if math.isfinite(value):
                out.append(value)
        except Exception:
            continue
    return out


def wilson_interval(successes, total, z=1.96):
    if total <= 0:
        return None, None
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denom
    return round(max(0.0, center - margin), 4), round(min(1.0, center + margin), 4)


def max_drawdown(results_pct):
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for result in results_pct:
        equity *= max(0.0, 1.0 + result / 100.0)
        peak = max(peak, equity)
        if peak:
            worst = min(worst, equity / peak - 1.0)
    return round(worst * 100.0, 3)


def summarize_closed_trades(rows):
    results = _values(rows)
    wins = [x for x in results if x > 0]
    losses = [x for x in results if x <= 0]
    n = len(results)
    low, high = wilson_interval(len(wins), n)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))

    return {
        "validation_version": VALIDATION_VERSION,
        "sample_size": n,
        "status": "MEASURED" if n >= 30 else "LEARNING",
        "win_rate_pct": round(len(wins) / n * 100.0, 2) if n else None,
        "win_rate_ci95_pct": (
            [round(low * 100.0, 2), round(high * 100.0, 2)] if low is not None else None
        ),
        "expectancy_pct": round(sum(results) / n, 4) if n else None,
        "profit_factor": (
            round(gross_profit / gross_loss, 4)
            if gross_loss > 0
            else (None if not wins else "INF")
        ),
        "average_win_pct": round(sum(wins) / len(wins), 4) if wins else None,
        "average_loss_pct": round(sum(losses) / len(losses), 4) if losses else None,
        "max_drawdown_pct": max_drawdown(results) if results else None,
    }


def calibrated_probability(score, historical_rows=None):
    """Bayesian score-band calibration; honest LEARNING state below 30 samples."""
    try:
        score = float(score)
    except Exception:
        score = 50.0
    band_low = int(max(0, min(100, score)) // 5 * 5)
    matching = []
    for row in historical_rows or []:
        try:
            row_score = float(row.get("score"))
            result = float(row.get("net_result_pct", row.get("result_pct")))
        except Exception:
            continue
        if band_low <= row_score < band_low + 5:
            matching.append(result)

    # Beta(2,2) shrinkage avoids 0%/100% claims with a small sample.
    wins = sum(1 for x in matching if x > 0)
    probability = (wins + 2.0) / (len(matching) + 4.0)
    return {
        "probability": round(probability, 4),
        "sample_size": len(matching),
        "status": "CALIBRATED" if len(matching) >= 30 else "LEARNING",
        "score_band": f"{band_low}-{band_low + 4}",
        "validation_version": VALIDATION_VERSION,
    }
