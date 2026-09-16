"""Fantasy-relevant positions shared across sources, matchup, decisions,
and backtest.

DEF/DST is intentionally excluded here: it's a team-level entry (keyed by
team abbreviation), not a per-player nflverse stat row, so it's handled
separately by whichever source provides it (Sleeper's projections endpoint
does).

IDP (individual defensive player) positions are included since this league
(and IDP leagues generally) start DL/LB/DB, not a team defense. nflverse's
weekly file and Sleeper's projections both use granular position codes
(e.g. "CB", "S" under the roster's generic "DB" slot) rather than the
roster-slot names themselves, so the eligibility map below translates
between the two.
"""

SKILL_POSITIONS = ["QB", "RB", "WR", "TE", "K"]

DL_POSITIONS = ["DE", "DT", "NT", "DL"]
LB_POSITIONS = ["LB", "ILB", "OLB", "MLB"]
DB_POSITIONS = ["CB", "S", "FS", "SS", "SAF", "DB"]
IDP_POSITIONS = DL_POSITIONS + LB_POSITIONS + DB_POSITIONS

# Every per-player position a source is expected to fetch/keep. DEF is
# handled separately (team-level), so it's not in this list.
PROJECTED_POSITIONS = SKILL_POSITIONS + IDP_POSITIONS

# Roster slot name -> set of player positions eligible to fill it. Used by
# the lineup optimizer (Stage 3). Matchup/backtest currently only cover
# offense (see matchup.py / evaluation/backtest.py docstrings) — IDP
# matchup dynamics (an IDP scores off the opposing *offense's* tendencies,
# not "points allowed by position" the same way) are a documented future
# enhancement, not silently skipped.
ROSTER_SLOT_ELIGIBILITY: dict[str, set[str]] = {
    "QB": {"QB"},
    "RB": {"RB"},
    "WR": {"WR"},
    "TE": {"TE"},
    "K": {"K"},
    "DEF": {"DEF"},
    "FLEX": {"RB", "WR", "TE"},
    "WRRB_FLEX": {"RB", "WR"},
    "REC_FLEX": {"WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
    "DL": set(DL_POSITIONS),
    "LB": set(LB_POSITIONS),
    "DB": set(DB_POSITIONS),
    "IDP_FLEX": set(IDP_POSITIONS),
}

# Canonical value-replacement groups: several granular positions can share
# one pooled replacement baseline where the roster slot itself doesn't
# distinguish between them (any of DE/DT/NT fills the "DL" slot
# identically) — pooling avoids a sparse, near-meaningless per-granular-
# position baseline (a given week's rostered players might have zero
# players tagged exactly "DT", say, even though plenty fill the DL slot).
# Offense positions are each their own singleton group: FLEX-type slots
# really do compete across genuinely different-scarcity positions
# (RB/WR/TE), so those stay split rather than pooled.
POSITION_GROUPS: dict[str, set[str]] = {
    "QB": {"QB"},
    "RB": {"RB"},
    "WR": {"WR"},
    "TE": {"TE"},
    "K": {"K"},
    "DEF": {"DEF"},
    "DL": set(DL_POSITIONS),
    "LB": set(LB_POSITIONS),
    "DB": set(DB_POSITIONS),
}

GRANULAR_TO_GROUP: dict[str, str] = {
    granular: group for group, granulars in POSITION_GROUPS.items() for granular in granulars
}
