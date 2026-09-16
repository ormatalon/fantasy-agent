"""Translates nflverse's raw defensive stat columns into Sleeper's IDP
scoring-key names, so `score_stats`-style weighting can be applied to
nflverse data the same way it's applied to Sleeper's own projections.

Not exhaustive — rarer categories (safeties, missing here) aren't in
nflverse's weekly file under an obvious column name, so nflverse-derived
IDP points slightly underweight them. Sleeper's own source (weighted 1.0
vs. nflverse's 0.5 in config.py) covers every category exactly, so this
approximation only affects the secondary blended signal, not the baseline.
"""

NFLVERSE_TO_SLEEPER_IDP: dict[str, str] = {
    "def_tackles_solo": "idp_tkl_solo",
    "def_tackle_assists": "idp_tkl_ast",
    "def_tackles_for_loss": "idp_tkl_loss",
    "def_sacks": "idp_sack",
    "def_interceptions": "idp_int",
    "def_pass_defended": "idp_pass_def",
    "fumble_recovery_opp": "idp_fum_rec",
    "def_fumbles_forced": "idp_ff",
    "def_tds": "idp_def_td",
}
# Summed together for idp_blk_kick (Sleeper doesn't distinguish which kick type).
BLOCKED_KICK_COLUMNS = ["def_punt_blocks", "def_pat_blocks", "def_fg_blocks"]


def compute_idp_points(row, scoring_settings: dict[str, float]) -> float:
    total = 0.0
    for nflverse_col, sleeper_key in NFLVERSE_TO_SLEEPER_IDP.items():
        total += (row.get(nflverse_col) or 0) * scoring_settings.get(sleeper_key, 0)
    blocked_kicks = sum(row.get(c) or 0 for c in BLOCKED_KICK_COLUMNS)
    total += blocked_kicks * scoring_settings.get("idp_blk_kick", 0)
    return total
