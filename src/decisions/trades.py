"""Trade evaluator: scores a proposed trade by the change in projected value.

Horizon-agnostic: it sums whatever projection table the caller passes, so
the same code evaluates a trade on this week's numbers or on full-season
totals (`engine.build_season_projection_table`). Callers should say which
horizon they used when reporting the verdict, since a trade that looks even
this week can be lopsided across a season.
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

    why = f"Give {give_total:.1f} proj pts, receive {receive_total:.1f} proj pts ({delta:+.1f})."
    return TradeEvaluation(
        give=give, receive=receive, give_total=give_total, receive_total=receive_total, delta=delta, verdict=verdict, why=why
    )
