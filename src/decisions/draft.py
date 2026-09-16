"""Real-time draft assistant: polls a live draft, tracks the board, and
proactively surfaces a ranked pick suggestion the instant it's my turn.

Also supports a --replay mode against a completed draft (see
run_draft_replay) so the suggestion logic can be sanity-checked without
waiting for the next real or mock draft.

Limitation (deliberate, MVP scope): pick value uses the projection engine's
week-1 output as a stand-in for season-long value, since Stage 1 only
projects one week at a time. Early-season projections correlate reasonably
with overall value, but this is an approximation, not a proper ADP/
season-aggregate model — flagged here rather than silently assumed correct.
Replay mode compounds this with a second, separate approximation: it scores
past picks using *today's* projections, not what was knowable on draft day
— useful for sanity-checking the ranking mechanism, not a true grade of
draft-day decisions (see run_draft_replay docstring).
"""

import time
from dataclasses import dataclass

from src.decisions.vorp import compute_replacement_levels, rank_by_vorp
from src.ingestion.sleeper import SleeperClient
from src.projections.engine import AdjustedProjection


@dataclass
class DraftState:
    draft_id: str
    teams: int
    rounds: int
    is_snake: bool
    my_slot: int | None


def resolve_draft_state(client: SleeperClient, draft_id: str, my_user_id: str) -> DraftState:
    """Works for any draft_id — a real league's draft or a standalone mock
    draft — since Sleeper's draft_order is always keyed by user_id, not
    tied to league rosters.
    """
    draft = client.get_draft(draft_id)
    settings = draft.get("settings") or {}
    teams = settings.get("teams") or 0
    rounds = settings.get("rounds") or 0
    is_snake = (draft.get("type") or "snake").lower() == "snake"
    draft_order = draft.get("draft_order") or {}
    my_slot = draft_order.get(my_user_id)
    return DraftState(draft_id=draft_id, teams=teams, rounds=rounds, is_snake=is_snake, my_slot=my_slot)


def find_league_draft_id(client: SleeperClient, league_id: str) -> str | None:
    drafts = client.get_league_drafts(league_id)
    return drafts[0]["draft_id"] if drafts else None


def pick_number_to_slot(pick_no: int, teams: int, is_snake: bool) -> int:
    """1-indexed overall pick number -> 1-indexed draft slot."""
    round_no = (pick_no - 1) // teams + 1
    pos_in_round = (pick_no - 1) % teams
    if is_snake and round_no % 2 == 0:
        pos_in_round = teams - 1 - pos_in_round
    return pos_in_round + 1


def is_my_turn(state: DraftState, picks_made: int) -> bool:
    if state.my_slot is None:
        return False
    return pick_number_to_slot(picks_made + 1, state.teams, state.is_snake) == state.my_slot


def suggest_pick(
    available: list[AdjustedProjection],
    roster_positions: list[str],
    num_teams: int,
    drafted_ids: set[str],
    limit: int = 10,
) -> list[tuple[AdjustedProjection, float]]:
    candidates = [p for p in available if p.player_id not in drafted_ids]
    projections_by_position: dict[str, list[float]] = {}
    for p in candidates:
        if p.position:
            projections_by_position.setdefault(p.position, []).append(p.mean)
    replacement_levels = compute_replacement_levels(roster_positions, num_teams, projections_by_position)
    return rank_by_vorp(candidates, replacement_levels)[:limit]


def run_draft_assistant(
    client: SleeperClient,
    build_table,  # callable: () -> list[AdjustedProjection], injected so this stays testable/pure elsewhere
    draft_id: str,
    my_user_id: str,
    roster_positions: list[str],
    poll_interval: int = 10,
) -> None:
    state = resolve_draft_state(client, draft_id, my_user_id)
    if state.my_slot is None:
        print("Could not determine your draft slot - are you a participant in this draft?")
        return

    print(f"Draft assistant active: draft {state.draft_id}, you are slot {state.my_slot} of {state.teams}.")
    print("Polling for your turn - leave this running. Ctrl+C to stop.\n")

    seen_picks = -1
    while True:
        picks = client.get_draft_picks(state.draft_id)
        picks_made = len(picks)

        if state.rounds and picks_made >= state.teams * state.rounds:
            print("Draft complete.")
            return

        if picks_made != seen_picks:
            seen_picks = picks_made
            if is_my_turn(state, picks_made):
                drafted_ids = {p["player_id"] for p in picks}
                table = build_table()
                suggestions = suggest_pick(table, roster_positions, state.teams, drafted_ids)
                print(f"\nYour turn - pick {picks_made + 1}:")
                for proj, vor in suggestions:
                    print(f"  {proj.name:<25}{(proj.position or ''):<5}{proj.mean:>6.1f}  VOR {vor:+.1f}")
                print()

        time.sleep(poll_interval)


def run_draft_replay(
    client: SleeperClient,
    build_table,
    draft_id: str,
    my_user_id: str,
    roster_positions: list[str],
    limit_per_pick: int = 5,
) -> None:
    """Steps through a COMPLETED draft's actual picks in order. At every
    pick that was mine, prints what the assistant would suggest today —
    ranked against only the players still undrafted at that point — and
    flags whether the pick I actually made was in that top list.

    Caveat printed once, not just documented: this uses *today's*
    projections applied retroactively, not draft-day information, so it is
    a sanity check of the ranking mechanism, not a fair grade of draft-day
    decisions (a rookie who broke out since would look like an obvious
    pick in hindsight it wasn't at the time).
    """
    state = resolve_draft_state(client, draft_id, my_user_id)
    if state.my_slot is None:
        print("Could not determine your draft slot - are you a participant in this draft?")
        return

    picks = sorted(client.get_draft_picks(draft_id), key=lambda p: p["pick_no"])
    if not picks:
        print("No picks found for this draft.")
        return

    table = build_table()
    by_id = {p.player_id: p for p in table}

    print(f"Replaying draft {draft_id} - you are slot {state.my_slot} of {state.teams}.")
    print("Using TODAY's projections applied retroactively - a sanity check of the ranking logic,")
    print("not a fair grade of draft-day decisions (hindsight bias).\n")

    drafted_ids: set[str] = set()
    my_picks_seen = 0
    for pick in picks:
        pick_no = pick["pick_no"]
        if pick_number_to_slot(pick_no, state.teams, state.is_snake) == state.my_slot:
            my_picks_seen += 1
            available = [p for p in table if p.player_id not in drafted_ids]
            suggestions = suggest_pick(available, roster_positions, state.teams, drafted_ids=set(), limit=limit_per_pick)

            actual_id = pick.get("player_id")
            meta = pick.get("metadata") or {}
            actual_name = f"{meta.get('first_name', '')} {meta.get('last_name', '')}".strip() or actual_id

            print(f"Pick {pick_no} (round {pick.get('round')}) - your turn:")
            for proj, vor in suggestions:
                marker = "  <-- actual pick" if proj.player_id == actual_id else ""
                print(f"  {proj.name:<25}{(proj.position or ''):<5}{proj.mean:>6.1f}  VOR {vor:+.1f}{marker}")
            if actual_id not in {p.player_id for p, _v in suggestions}:
                actual_proj = by_id.get(actual_id)
                shown = f"{actual_proj.mean:.1f} pts today" if actual_proj else "no current projection"
                print(f"  (Actual pick: {actual_name} - not in top {limit_per_pick}, {shown})")
            print()

        drafted_ids.add(pick.get("player_id"))

    if my_picks_seen == 0:
        print("You had no picks in this draft (slot mismatch?).")
