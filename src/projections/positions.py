"""Fantasy-relevant positions shared across sources, matchup, and backtest.

DEF/DST is intentionally excluded here: it's a team-level entry (keyed by
team abbreviation), not a per-player nflverse stat row, so it's handled
separately by whichever source provides it (Sleeper's projections endpoint
does).
"""

SKILL_POSITIONS = ["QB", "RB", "WR", "TE", "K"]
