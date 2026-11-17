"""The 32 NFL teams. Sleeper's abbreviation is the key; ESPN and nflverse differ for a few."""

# (sleeper abbr, city, nickname, espn id, espn abbr, nflverse code)
_TEAMS = [
    ("ARI", "Arizona", "Cardinals", "22", "ARI", "ARI"),
    ("ATL", "Atlanta", "Falcons", "1", "ATL", "ATL"),
    ("BAL", "Baltimore", "Ravens", "33", "BAL", "BAL"),
    ("BUF", "Buffalo", "Bills", "2", "BUF", "BUF"),
    ("CAR", "Carolina", "Panthers", "29", "CAR", "CAR"),
    ("CHI", "Chicago", "Bears", "3", "CHI", "CHI"),
    ("CIN", "Cincinnati", "Bengals", "4", "CIN", "CIN"),
    ("CLE", "Cleveland", "Browns", "5", "CLE", "CLE"),
    ("DAL", "Dallas", "Cowboys", "6", "DAL", "DAL"),
    ("DEN", "Denver", "Broncos", "7", "DEN", "DEN"),
    ("DET", "Detroit", "Lions", "8", "DET", "DET"),
    ("GB", "Green Bay", "Packers", "9", "GB", "GB"),
    ("HOU", "Houston", "Texans", "34", "HOU", "HOU"),
    ("IND", "Indianapolis", "Colts", "11", "IND", "IND"),
    ("JAX", "Jacksonville", "Jaguars", "30", "JAX", "JAX"),
    ("KC", "Kansas City", "Chiefs", "12", "KC", "KC"),
    ("LAC", "Los Angeles", "Chargers", "24", "LAC", "LAC"),
    ("LAR", "Los Angeles", "Rams", "14", "LAR", "LA"),
    ("LV", "Las Vegas", "Raiders", "13", "LV", "LV"),
    ("MIA", "Miami", "Dolphins", "15", "MIA", "MIA"),
    ("MIN", "Minnesota", "Vikings", "16", "MIN", "MIN"),
    ("NE", "New England", "Patriots", "17", "NE", "NE"),
    ("NO", "New Orleans", "Saints", "18", "NO", "NO"),
    ("NYG", "New York", "Giants", "19", "NYG", "NYG"),
    ("NYJ", "New York", "Jets", "20", "NYJ", "NYJ"),
    ("PHI", "Philadelphia", "Eagles", "21", "PHI", "PHI"),
    ("PIT", "Pittsburgh", "Steelers", "23", "PIT", "PIT"),
    ("SF", "San Francisco", "49ers", "25", "SF", "SF"),
    ("SEA", "Seattle", "Seahawks", "26", "SEA", "SEA"),
    ("TB", "Tampa Bay", "Buccaneers", "27", "TB", "TB"),
    ("TEN", "Tennessee", "Titans", "10", "TEN", "TEN"),
    ("WAS", "Washington", "Commanders", "28", "WSH", "WAS"),
]

TEAMS = [
    {"abbr": a, "name": f"{city} {nick}", "city": city, "nickname": nick, "espn_id": eid,
     "espn_abbr": eabbr, "nflverse_code": code}
    for a, city, nick, eid, eabbr, code in _TEAMS
]

_ALIASES: dict[str, str] = {}
for t in TEAMS:
    for alias in (t["abbr"], t["espn_abbr"], t["nflverse_code"], t["name"], t["nickname"]):
        _ALIASES[alias.lower()] = t["abbr"]
    # a city alone resolves only when one NFL team has it ("Seattle" yes, "New York" no)
for t in TEAMS:
    if sum(u["city"] == t["city"] for u in TEAMS) == 1:
        _ALIASES[t["city"].lower()] = t["abbr"]
_ALIASES["jac"] = "JAX"

BY_ABBR = {t["abbr"]: t for t in TEAMS}


def resolve(text: str | None) -> str | None:
    """Sleeper abbreviation for an abbreviation, city or nickname in any source's spelling, else None."""
    return _ALIASES.get((text or "").strip().lower())
