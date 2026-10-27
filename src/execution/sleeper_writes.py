"""Enacts approved moves on Sleeper through its GraphQL API.

Sleeper has no documented write API; these are the mutations the Sleeper
web app sends (see ingestion/sleeper_graphql.py). Every function here:

- calls `verify` first, so nothing is written without an `Approval` issued
  by execution/approval.py for exactly this payload;
- needs SLEEPER_TOKEN (gql raises before sending anything without it);
- reads the result back through a different GraphQL query than the one that
  wrote - never REST, which is cached and can show the pre-write state for
  minutes. A read-back that fails is reported as unverified (None), not as a
  failed write.

Trades are deliberately absent: they reach another manager, and in leagues
with no review period an accepted trade executes at once.
"""

from src.execution.approval import Approval, ProposedWrite, verify
from src.ingestion import storage
from src.ingestion.sleeper import LIVE_ROSTERS_QUERY
from src.ingestion.sleeper_graphql import SleeperError, gql

EMPTY_SLOT = "0"

TRANSACTION_FIELDS = "transaction_id type status leg roster_ids adds drops settings created"

UPDATE_MATCHUP_LEG = (
    "mutation update_matchup_leg($round: Int!, $leg: Int!, $league_id: Snowflake!, $roster_id: Int!, $starters: [String]) {"
    " update_matchup_leg(round: $round, leg: $leg, league_id: $league_id, roster_id: $roster_id, starters: $starters)"
    " { roster_id starters } }"
)
ROSTER_UPDATE_STARTERS = (
    "mutation roster_update_starters($league_id: Snowflake!, $roster_id: Int!, $starters: [String]) {"
    " roster_update_starters(league_id: $league_id, roster_id: $roster_id, starters: $starters) { roster_id starters } }"
)
MATCHUP_LEGS = (
    "query matchup_legs($round: Int!, $league_id: Snowflake!) {"
    " matchup_legs(round: $round, league_id: $league_id) { roster_id starters } }"
)
ROSTER_UPDATE_RESERVE = (
    "mutation roster_update_reserve($league_id: Snowflake!, $roster_id: Int!, $reserve: [String]) {"
    " roster_update_reserve(league_id: $league_id, roster_id: $roster_id, reserve: $reserve) { roster_id reserve } }"
)
CREATE_TRANSACTION = (
    "mutation league_create_transaction($league_id: Snowflake!, $type: String!, $k_adds: [String], $v_adds: [Int],"
    " $k_drops: [String], $v_drops: [Int]) {"
    " league_create_transaction(league_id: $league_id, type: $type, k_adds: $k_adds, v_adds: $v_adds,"
    f" k_drops: $k_drops, v_drops: $v_drops) {{ {TRANSACTION_FIELDS} }} }}"
)
SUBMIT_WAIVER_CLAIM = (
    "mutation submit_waiver_claim($league_id: Snowflake!, $k_adds: [String], $v_adds: [Int], $k_drops: [String],"
    " $v_drops: [Int], $k_settings: [String], $v_settings: [Int]) {"
    " submit_waiver_claim(league_id: $league_id, k_adds: $k_adds, v_adds: $v_adds, k_drops: $k_drops,"
    f" v_drops: $v_drops, k_settings: $k_settings, v_settings: $v_settings) {{ {TRANSACTION_FIELDS} }} }}"
)
CANCEL_WAIVER_CLAIM = (
    "mutation cancel_waiver_claim($league_id: Snowflake!, $transaction_id: Snowflake!, $leg: Int!) {"
    f" cancel_waiver_claim(league_id: $league_id, transaction_id: $transaction_id, leg: $leg) {{ {TRANSACTION_FIELDS} }} }}"
)
PENDING_TRANSACTIONS = (
    "query league_transactions_filtered($league_id: Snowflake!, $roster_id_filters: [Int]) {"
    " league_transactions_filtered(league_id: $league_id, status_filters: [\"pending\"],"
    f" roster_id_filters: $roster_id_filters, limit: 50) {{ {TRANSACTION_FIELDS} }} }}"
)


def _live_roster(league_id: str, roster_id: int) -> dict | None:
    try:
        rosters = gql("league_rosters", LIVE_ROSTERS_QUERY, {"league_id": league_id}).get("league_rosters") or []
    except SleeperError:
        return None
    return next((r for r in rosters if r["roster_id"] == roster_id), None)


def _paired(ids: list[str], roster_id: int) -> tuple[list[str], list[int]]:
    """Sleeper takes {player: roster} maps as parallel k_/v_ arrays."""
    return list(ids), [roster_id] * len(ids)


def set_lineup(
    league_id: str, roster_id: int, week: int, starters: list[str], write: ProposedWrite, approval: Approval
) -> dict[str, list[str] | SleeperError | None]:
    """Set the week's full starters list (player ids in `roster_positions`
    order, EMPTY_SLOT for an empty one).

    Sends it both to the week's matchup leg and to the roster. Two published
    reverse-engineerings of this API disagree on which of the two Sleeper
    scores from, so both are set to the same list and both are read back.
    Returns {"matchup": ..., "roster": ...}, each the starters read back,
    None if only the read-back failed, or the SleeperError if that write
    itself was refused - one can land while the other is refused, and the
    caller must be able to say which."""
    verify(approval, write)
    out: dict[str, list[str] | SleeperError | None] = {}
    try:
        gql("update_matchup_leg", UPDATE_MATCHUP_LEG, {
            "round": week, "leg": week, "league_id": league_id, "roster_id": roster_id, "starters": starters,
        }, auth=True)
    except SleeperError as e:
        out["matchup"] = e
    else:
        try:
            legs = gql("matchup_legs", MATCHUP_LEGS, {"round": week, "league_id": league_id}, auth=True)
            out["matchup"] = next(
                (leg["starters"] for leg in legs.get("matchup_legs") or [] if leg["roster_id"] == roster_id), None
            )
        except SleeperError:
            out["matchup"] = None

    try:
        gql("roster_update_starters", ROSTER_UPDATE_STARTERS, {
            "league_id": league_id, "roster_id": roster_id, "starters": starters,
        }, auth=True)
    except SleeperError as e:
        out["roster"] = e
    else:
        roster = _live_roster(league_id, roster_id)
        out["roster"] = roster["starters"] if roster else None
    return out


def set_reserve(league_id: str, roster_id: int, reserve: list[str], write: ProposedWrite, approval: Approval) -> list[str] | None:
    """Set the COMPLETE list of players in IR slots (not a delta). Returns
    the IR list read back, or None if the read-back failed."""
    verify(approval, write)
    gql("roster_update_reserve", ROSTER_UPDATE_RESERVE, {
        "league_id": league_id, "roster_id": roster_id, "reserve": reserve,
    }, auth=True)
    roster = _live_roster(league_id, roster_id)
    return (roster.get("reserve") or []) if roster else None


def add_drop(
    league_id: str, roster_id: int, add: str | None, drop: str | None, write: ProposedWrite, approval: Approval
) -> list[str] | None:
    """Immediate free-agent add and/or drop. Returns my roster's players read
    back, or None if the read-back failed."""
    verify(approval, write)
    k_adds, v_adds = _paired([add] if add else [], roster_id)
    k_drops, v_drops = _paired([drop] if drop else [], roster_id)
    gql("league_create_transaction", CREATE_TRANSACTION, {
        "league_id": league_id, "type": "free_agent",
        "k_adds": k_adds, "v_adds": v_adds, "k_drops": k_drops, "v_drops": v_drops,
    }, auth=True)
    roster = _live_roster(league_id, roster_id)
    return (roster.get("players") or []) if roster else None


def submit_claim(
    league_id: str, roster_id: int, add: str, drop: str | None, bid: int | None,
    write: ProposedWrite, approval: Approval,
) -> dict:
    """Waiver claim, processed at the league's waiver run. `bid` only for
    FAAB leagues. Returns the created transaction."""
    verify(approval, write)
    k_adds, v_adds = _paired([add], roster_id)
    k_drops, v_drops = _paired([drop] if drop else [], roster_id)
    settings = {"k_settings": ["waiver_bid"], "v_settings": [bid]} if bid is not None else {}
    data = gql("submit_waiver_claim", SUBMIT_WAIVER_CLAIM, {
        "league_id": league_id, "k_adds": k_adds, "v_adds": v_adds, "k_drops": k_drops, "v_drops": v_drops, **settings,
    }, auth=True)
    return data.get("submit_waiver_claim") or {}


def cancel_claim(league_id: str, transaction_id: str, leg: int, write: ProposedWrite, approval: Approval) -> dict:
    verify(approval, write)
    data = gql("cancel_waiver_claim", CANCEL_WAIVER_CLAIM, {
        "league_id": league_id, "transaction_id": transaction_id, "leg": leg,
    }, auth=True)
    return data.get("cancel_waiver_claim") or {}


def pending_claims(league_id: str, roster_id: int) -> list[dict]:
    """My pending waiver claims. Needs the token: Sleeper doesn't show
    pending transactions publicly."""
    data = gql("league_transactions_filtered", PENDING_TRANSACTIONS, {
        "league_id": league_id, "roster_id_filters": [roster_id],
    }, auth=True)
    return [t for t in data.get("league_transactions_filtered") or [] if t.get("type") == "waiver"]


def describe_claim(conn, txn: dict) -> str:
    adds = ", ".join(storage.player_name(conn, pid) for pid in (txn.get("adds") or {}))
    drops = ", ".join(storage.player_name(conn, pid) for pid in (txn.get("drops") or {}))
    bid = (txn.get("settings") or {}).get("waiver_bid")
    return (f"{txn['transaction_id']}  claim {adds or '-'}"
            + (f", drop {drops}" if drops else "") + (f", bid ${bid}" if bid is not None else ""))
