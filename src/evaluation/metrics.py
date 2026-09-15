"""Accuracy metrics over paired (predicted, actual) values."""

from dataclasses import dataclass
from math import sqrt


@dataclass
class AccuracyReport:
    n: int
    mae: float
    rmse: float


def compute_metrics(errors: list[float]) -> AccuracyReport:
    """`errors` is predicted - actual, one entry per player-week."""
    n = len(errors)
    if n == 0:
        return AccuracyReport(n=0, mae=0.0, rmse=0.0)
    mae = sum(abs(e) for e in errors) / n
    rmse = sqrt(sum(e**2 for e in errors) / n)
    return AccuracyReport(n=n, mae=mae, rmse=rmse)
