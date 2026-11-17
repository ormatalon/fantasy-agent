"""Read-only decisions over the projection artifact and the stored league (docs/data-contract.md section 9).

One public function per agent tool; each takes (conn, league_id, ...) and returns the contract's result dict.
"""

from decisions.league import NoData
from decisions.lineup import recommend_lineup
from decisions.projection_tool import get_projections
from decisions.results import get_season_leaders, get_week_results
from decisions.stash import get_injured_stash
from decisions.trending import get_trending_players
from decisions.trades import evaluate_trade, find_trade_ideas
from decisions.waivers import get_waiver_targets

PROVIDERS = {
    "get_projections": get_projections,
    "get_season_leaders": get_season_leaders,
    "recommend_lineup": recommend_lineup,
    "get_waiver_targets": get_waiver_targets,
    "get_injured_stash": get_injured_stash,
    "evaluate_trade": evaluate_trade,
    "find_trade_ideas": find_trade_ideas,
    "get_week_results": get_week_results,
    "get_trending_players": get_trending_players,
}
