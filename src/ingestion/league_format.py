"""LeagueInfo and LeagueFormat from raw Sleeper league json (docs/data-contract.md section 8)."""

IDP_SLOTS = {"DL", "DE", "DT", "LB", "DB", "CB", "S", "IDP_FLEX"}
RESERVE_ALLOW = {"na": "NA", "doubtful": "Doubtful", "dnr": "DNR", "out": "Out", "sus": "Sus", "cov": "COV"}
WAIVER_TYPES = {0: "rolling", 1: "reverse_standings", 2: "faab"}


def unsupported_reason(league: dict) -> str | None:
    """Guillotine has no Sleeper setting we know of (docs/spikes.md, O2), so it is not detected."""
    settings = league["settings"]
    if settings.get("type") == 1:
        return "keeper"
    if settings.get("type") == 2:
        return "dynasty"
    if settings.get("best_ball") == 1:
        return "best_ball"
    return None


def defense(positions: list[str]) -> str:
    if IDP_SLOTS & set(positions):
        return "idp"
    return "team_def" if "DEF" in positions else "none"


def ir_allowed(settings: dict) -> list[str]:
    """Designations that may sit in an IR slot: "IR" and "PUP" always, plus one per reserve_allow_* key set to 1."""
    if not settings.get("reserve_slots"):
        return []
    return ["IR", "PUP", *(label for key, label in RESERVE_ALLOW.items() if settings.get(f"reserve_allow_{key}") == 1)]


def league_format(league: dict, fantasy_teams: list[dict] | None = None) -> dict:
    """fantasy_teams: rows with roster_id and waiver_budget_used, for the FAAB balance."""
    settings, scoring = league["settings"], league["scoring_settings"]
    positions = league["roster_positions"]
    slots = [p for p in positions if p != "BN"]
    waiver_type = WAIVER_TYPES.get(settings.get("waiver_type", 0), "rolling")
    budget = settings.get("waiver_budget") if waiver_type == "faab" else None
    reason = unsupported_reason(league)
    return {
        "league_id": league["league_id"],
        "season": int(league["season"]),
        "supported": reason is None,
        "unsupported_reason": reason,
        "scoring": scoring,
        "reception_points": scoring.get("rec", 0.0),
        "te_premium": scoring.get("bonus_rec_te", 0.0),
        "defense": defense(positions),
        "superflex": "SUPER_FLEX" in positions,
        "kicker": "K" in positions,
        "slots": slots,
        "bench_size": positions.count("BN"),
        "ir_slots": settings.get("reserve_slots", 0),
        "ir_allowed_designations": ir_allowed(settings),
        "taxi_slots": settings.get("taxi_slots", 0),
        "num_fantasy_teams": settings.get("num_teams", league.get("total_rosters")),
        "waiver_type": waiver_type,
        "faab_budget": budget,
        "faab_remaining": (
            {t["roster_id"]: budget - (t["waiver_budget_used"] or 0) for t in fantasy_teams or []}
            if budget is not None
            else {}
        ),
        "trade_deadline_week": settings.get("trade_deadline") or None,
        "playoff_start_week": settings.get("playoff_week_start"),
        "roster_size": len([p for p in positions if p not in ("IR", "TAXI")]),  # active limit: starters + bench
    }


def league_info(league: dict) -> dict:
    """The item shape of GET /api/leagues (docs/api-contract.md)."""
    fmt = league_format(league)
    status = league["status"]
    not_selectable = fmt["unsupported_reason"] or (status if status != "in_season" else None)
    return {
        "league_id": league["league_id"],
        "name": league["name"],
        "season": fmt["season"],
        "status": status,
        "num_fantasy_teams": fmt["num_fantasy_teams"],
        "defense": fmt["defense"],
        "reception_points": fmt["reception_points"],
        "supported": fmt["supported"],
        "unsupported_reason": fmt["unsupported_reason"],
        "selectable": not_selectable is None,
        "not_selectable_reason": not_selectable,
    }
