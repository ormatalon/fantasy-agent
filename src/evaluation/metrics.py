"""Error metrics for the backtest."""

import math
from statistics import mean, stdev


def summarize(pairs: list[tuple[float, float]]) -> dict:
    """pairs: (prediction, actual)."""
    errors = [p - a for p, a in pairs]
    return {
        "n": len(errors),
        "mae": mean(abs(e) for e in errors),
        "rmse": math.sqrt(mean(e * e for e in errors)),
        "bias": mean(errors),
    }


def paired_abs_diff(pred: list[float], base: list[float], actual: list[float]) -> dict:
    """Mean of (|pred error| - |baseline error|) with a 95% interval; negative means pred is better."""
    d = [abs(p - a) - abs(b - a) for p, b, a in zip(pred, base, actual)]
    se = stdev(d) / math.sqrt(len(d))
    return {"mean_diff": mean(d), "ci95": [mean(d) - 1.96 * se, mean(d) + 1.96 * se]}


def calibration(rows: list[dict]) -> dict:
    """rows: position, pred, variance, actual. Per position: mean predicted variance vs mean squared error
    (ratio near 1 means the variance is honest) and the share of actuals within +-1 sd (68% if normal)."""
    out = {}
    for pos in sorted({r["position"] for r in rows}):
        sub = [r for r in rows if r["position"] == pos]
        mse = mean((r["pred"] - r["actual"]) ** 2 for r in sub)
        mvar = mean(r["variance"] for r in sub)
        within = mean(abs(r["pred"] - r["actual"]) <= math.sqrt(r["variance"]) for r in sub)
        out[pos] = {"n": len(sub), "mean_variance": mvar, "mse": mse, "ratio": mvar / mse, "within_1sd": within}
    return out
