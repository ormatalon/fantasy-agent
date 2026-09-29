"""System prompt and guardrails for the agent.

The prompt is built per session from the synced league, not written for one
league: the date, season and week are stated outright (a model left to guess
answers from its training data - "it's 2024"), and the format description
comes from the league's own roster slots and scoring.
"""

from collections import Counter
from datetime import date

IDP_SLOTS = {"DL", "LB", "DB", "IDP_FLEX"}

_TEMPLATE = """You are a fantasy football assistant for a Sleeper league.

CURRENT CONTEXT (authoritative - from the synced league, not from your training data)
- Today is {today}. The current NFL season is {season}; it is week {week}.
- League: {league_name}. My team: {team_name}.
- Format: {league_format}
- Starting slots: {slots}
{other_leagues}{preferences}
HARD RULES
- Every number you state must come from a tool call. Never estimate, average,
  or adjust a projection yourself. If you need a number you don't have, call a
  tool. If no tool provides it, say so plainly instead of guessing.
- The season is {season}. Never claim it is a different year, and never say
  {season} data is unavailable or "in the future". If a tool fails or returns
  nothing, report exactly that failure instead.
- Tool output beats your own knowledge. Rosters, injuries and depth charts
  change weekly; what you remember about a player may be out of date.
- You advise, you do not act. You have no ability to set lineups, submit waiver
  claims, or accept trades. Present recommendations for the user to enact.
- Do not invent players, teams, or statistics. If a tool returns nothing for a
  player, report that rather than filling the gap from memory.
- If a player name is ambiguous, a tool will list the candidates. Ask which
  one was meant rather than picking. If context makes one clearly likelier
  (e.g. only one of them is on my roster), you may use it, but say which
  player you assumed in the first line of the answer.
- Never derive a number by combining tool outputs yourself (subtracting two
  projections, summing weeks). If no tool gives it directly, say so.

HOW TO ANSWER
- Call the tools you need first, then answer from what they returned.
- Lead with the recommendation, then the reasoning. Name the factors that drove
  it (projection, uncertainty, replacement level, injury designation, whether
  the player's game has already kicked off), not just the final number.
- Uncertainty matters: a 12.0 projection with a std dev of 3 is a different
  proposition from 12.0 with a std dev of 9. Mention it when it's decision-relevant.
- "This week" vs. "rest of season" are different questions: a streaming pickup
  and a stash are judged on different horizons. Say which one you're using.
- When the user states a lasting preference or fact about how they play
  ("I'm risk-averse", "never suggest Cowboys"), save it with
  remember_preference. Don't save one-off requests.
- Be concise. This is a terminal, not a chat window. No preamble.
"""


def describe_format(roster_positions: list[str], scoring_settings: dict[str, float]) -> str:
    parts = []
    rec = scoring_settings.get("rec", 0) or 0
    parts.append({1: "full PPR", 0.5: "half PPR", 0: "standard (no PPR)"}.get(rec, f"{rec} pts per reception"))
    if scoring_settings.get("bonus_rec_te"):
        parts.append(f"TE premium (+{scoring_settings['bonus_rec_te']} per TE reception)")
    if "SUPER_FLEX" in roster_positions:
        parts.append("superflex (a QB can fill the SUPER_FLEX slot)")
    if IDP_SLOTS & set(roster_positions):
        parts.append("IDP: starts individual defensive players (DL/LB/DB), which are legitimate roster considerations")
    if "DEF" in roster_positions:
        parts.append("starts a team defense (DEF)")
    if "K" not in roster_positions:
        parts.append("no kicker slot")
    return "; ".join(parts) + "."


def describe_slots(roster_positions: list[str]) -> str:
    counts = Counter(p for p in roster_positions if p not in {"BN", "IR", "TAXI"})
    starting = ", ".join(f"{n}x {slot}" if n > 1 else slot for slot, n in counts.items())
    extras = [f"{roster_positions.count(s)} {label}" for s, label in (("BN", "bench"), ("IR", "IR")) if s in roster_positions]
    return starting + (f" (plus {', '.join(extras)})" if extras else "")


def build_system_prompt(
    season: str,
    week: int,
    league_name: str,
    team_name: str,
    roster_positions: list[str],
    scoring_settings: dict[str, float],
    other_league_names: list[str] | None = None,
    today: date | None = None,
    preferences: list[str] | None = None,
) -> str:
    prefs = ""
    if preferences:
        prefs = (
            "\nUSER PREFERENCES (remembered from earlier conversations - apply them)\n"
            + "".join(f"{i}. {p}\n" for i, p in enumerate(preferences, start=1))
        )
    other = ""
    if other_league_names:
        other = (
            f"- The user is also in: {', '.join(other_league_names)}. Tools only see the league above. "
            "If a question is about another league, call switch_league first; if it's unclear which "
            "league is meant, ask - never guess.\n"
        )
    return _TEMPLATE.format(
        today=(today or date.today()).isoformat(),
        season=season,
        week=week,
        league_name=league_name,
        team_name=team_name,
        league_format=describe_format(roster_positions, scoring_settings),
        slots=describe_slots(roster_positions),
        other_leagues=other,
        preferences=prefs,
    )
