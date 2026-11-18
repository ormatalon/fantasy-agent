"""Names as the manager said them -> ids. Never guesses: more than one fit is returned as candidates."""

import re
import sqlite3

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# Sleeper abbreviations with city and nickname, for "Seattle" / "Seahawks" -> SEA.
NFL_TEAMS = {
    "ARI": ("Arizona", "Cardinals"), "ATL": ("Atlanta", "Falcons"), "BAL": ("Baltimore", "Ravens"),
    "BUF": ("Buffalo", "Bills"), "CAR": ("Carolina", "Panthers"), "CHI": ("Chicago", "Bears"),
    "CIN": ("Cincinnati", "Bengals"), "CLE": ("Cleveland", "Browns"), "DAL": ("Dallas", "Cowboys"),
    "DEN": ("Denver", "Broncos"), "DET": ("Detroit", "Lions"), "GB": ("Green Bay", "Packers"),
    "HOU": ("Houston", "Texans"), "IND": ("Indianapolis", "Colts"), "JAX": ("Jacksonville", "Jaguars"),
    "KC": ("Kansas City", "Chiefs"), "LAC": ("Los Angeles", "Chargers"), "LAR": ("Los Angeles", "Rams"),
    "LV": ("Las Vegas", "Raiders"), "MIA": ("Miami", "Dolphins"), "MIN": ("Minnesota", "Vikings"),
    "NE": ("New England", "Patriots"), "NO": ("New Orleans", "Saints"), "NYG": ("New York", "Giants"),
    "NYJ": ("New York", "Jets"), "PHI": ("Philadelphia", "Eagles"), "PIT": ("Pittsburgh", "Steelers"),
    "SEA": ("Seattle", "Seahawks"), "SF": ("San Francisco", "49ers"), "TB": ("Tampa Bay", "Buccaneers"),
    "TEN": ("Tennessee", "Titans"), "WAS": ("Washington", "Commanders"),
}
ALIASES = {"LA": "LAR", "JAC": "JAX", "WSH": "WAS", "KAN": "KC", "NWE": "NE", "NOR": "NO", "SFO": "SF", "TAM": "TB",
           "GNB": "GB", "LVR": "LV"}


def norm(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", "", name.lower().replace("-", " ")).split()
    return " ".join(w for w in words if w not in SUFFIXES)


def nfl_team(text: str) -> list[str]:
    """Matching NFL team abbreviations: one, several ("New York") or none."""
    q = norm(text)
    if q.upper() in NFL_TEAMS:
        return [q.upper()]
    if q.upper() in ALIASES:
        return [ALIASES[q.upper()]]
    q = q.removeprefix("the ").removesuffix("s")
    hits = []
    for abbr, (city, nick) in NFL_TEAMS.items():
        forms = {norm(x).removesuffix("s") for x in (city, nick, f"{city} {nick}")}
        if q in forms:
            hits.append(abbr)
    return hits


def players(conn: sqlite3.Connection, league_id: str, name: str, my_roster_id: int | None) -> list[sqlite3.Row]:
    """Catalog rows for a name. One row = resolved; several = candidates (best first); none = not found.

    Exact full name first; several exact matches narrow to the one rostered in this league, then to the
    one on an NFL team. A partial name ("Williams") that fits several players is always returned as
    candidates, so the model asks or says which one it assumed.
    """
    q = norm(name)
    if not q:
        return []
    held = {
        r["player_id"]: r["roster_id"]
        for r in conn.execute("select player_id, roster_id from roster_player where league_id = ?", (league_id,))
    }
    rows = conn.execute("select * from player where full_name is not null").fetchall()

    def rank(r):
        roster = held.get(r["player_id"])
        return (roster != my_roster_id or roster is None, roster is None, r["nfl_team"] is None,
                r["search_rank"] or 10**9)

    exact = [r for r in rows if norm(r["full_name"]) == q]
    if exact:
        for keep in (lambda r: r["player_id"] in held, lambda r: r["nfl_team"] is not None):
            narrowed = [r for r in exact if keep(r)]
            if len(narrowed) == 1:
                return narrowed
        return sorted(exact, key=rank)
    words = q.split()
    partial = [r for r in rows if all(w in norm(r["full_name"]).split() for w in words)]
    return sorted(partial, key=rank)[:8]


def fantasy_team(teams: list[sqlite3.Row], name: str) -> list[sqlite3.Row]:
    """Fantasy teams for a name ("Ravid's RAMS", "ravid"): exact first, then every name containing it."""
    q = norm(name).removesuffix("s") if name.lower().endswith("'s") else norm(name)
    exact = [t for t in teams if norm(t["name"]) in (norm(name), q)]
    if exact:
        return exact
    return [t for t in teams if q in norm(t["name"])]
