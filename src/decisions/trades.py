"""Trade evaluator: scores a proposed trade by the change in projected value.

Limitation (deliberate, MVP scope): value is the current week's adjusted
projection, not a rest-of-season aggregate — the projection engine is
weekly-only (Stage 1), so this is a snapshot proxy, not dynasty-grade trade
value. Good enough for "is this a fair in-season trade" at a glance; a
season-long value model would be a real future addition, not a quick fix.
"""

from dataclasses import dataclass

from src.projections.engine import AdjustedProjection

FAVORABLE_THRESHOLD = 1.0  # points of delta before calling it favorable/unfavorable vs. "roughly even"


@dataclass
class TradeEvaluation:
    give: list[AdjustedProjection]
    receive: list[AdjustedProjection]
    give_total: float
    receive_total: float
    delta: float
    verdict: str
    why: str


def evaluate_trade(
    give_ids: list[str], receive_ids: list[str], projections: dict[str, AdjustedProjection]
) -> TradeEvaluation:
    give = [projections[pid] for pid in give_ids if pid in projections]
    receive = [projections[pid] for pid in receive_ids if pid in projections]

    give_total = sum(p.mean for p in give)
    receive_total = sum(p.mean for p in receive)
    delta = receive_total - give_total

    if delta > FAVORABLE_THRESHOLD:
        verdict = "favorable"
    elif delta < -FAVORABLE_THRESHOLD:
        verdict = "unfavorable"
    else:
        verdict = "roughly even"

    why = (
        f"Give {give_total:.1f} proj pts, receive {receive_total:.1f} proj pts "
        f"({delta:+.1f}) - based on this week's projections, not rest-of-season value."
    )
    return TradeEvaluation(
        give=give, receive=receive, give_total=give_total, receive_total=receive_total, delta=delta, verdict=verdict, why=why
    )
