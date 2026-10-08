"""NFL team names -> the abbreviations Sleeper uses in `players.team`.

Lets a question name a team the way people do ("Seattle", "Seahawks",
"sea") and still hit the synced catalog, instead of the model listing a
roster from memory.
"""

# Sleeper abbreviation -> (city, nickname)
NFL_TEAMS: dict[str, tuple[str, str]] = {
    "ARI": ("Arizona", "Cardinals"),
    "ATL": ("Atlanta", "Falcons"),
    "BAL": ("Baltimore", "Ravens"),
    "BUF": ("Buffalo", "Bills"),
    "CAR": ("Carolina", "Panthers"),
    "CHI": ("Chicago", "Bears"),
    "CIN": ("Cincinnati", "Bengals"),
    "CLE": ("Cleveland", "Browns"),
    "DAL": ("Dallas", "Cowboys"),
    "DEN": ("Denver", "Broncos"),
    "DET": ("Detroit", "Lions"),
    "GB": ("Green Bay", "Packers"),
    "HOU": ("Houston", "Texans"),
    "IND": ("Indianapolis", "Colts"),
    "JAX": ("Jacksonville", "Jaguars"),
    "KC": ("Kansas City", "Chiefs"),
    "LAC": ("Los Angeles", "Chargers"),
    "LAR": ("Los Angeles", "Rams"),
    "LV": ("Las Vegas", "Raiders"),
    "MIA": ("Miami", "Dolphins"),
    "MIN": ("Minnesota", "Vikings"),
    "NE": ("New England", "Patriots"),
    "NO": ("New Orleans", "Saints"),
    "NYG": ("New York", "Giants"),
    "NYJ": ("New York", "Jets"),
    "PHI": ("Philadelphia", "Eagles"),
    "PIT": ("Pittsburgh", "Steelers"),
    "SEA": ("Seattle", "Seahawks"),
    "SF": ("San Francisco", "49ers"),
    "TB": ("Tampa Bay", "Buccaneers"),
    "TEN": ("Tennessee", "Titans"),
    "WAS": ("Washington", "Commanders"),
}

# Other spellings people (and older data) use.
_ALIASES = {
    "JAC": "JAX", "WSH": "WAS", "LA": "LAR", "SD": "LAC", "OAK": "LV", "STL": "LAR",
    "NINERS": "SF", "BUCS": "TB", "JAGS": "JAX", "PATS": "NE", "PACK": "GB",
}


def nfl_team_label(abbr: str) -> str:
    city, nickname = NFL_TEAMS[abbr]
    return f"{city} {nickname} ({abbr})"


def resolve_nfl_team(query: str) -> list[str]:
    """Abbreviations matching `query`: one when it's clear, several when
    ambiguous ("New York"), none when unknown. Matches an abbreviation,
    city, nickname or "city nickname", case-insensitive."""
    q = " ".join(query.lower().replace(".", "").split())
    if q.startswith("the "):
        q = q[4:]
    if q.upper() in NFL_TEAMS:
        return [q.upper()]
    if q.upper() in _ALIASES:
        return [_ALIASES[q.upper()]]
    for abbr, (city, nickname) in NFL_TEAMS.items():
        nick = nickname.lower()
        if q in (nick, nick.rstrip("s"), f"{city.lower()} {nick}"):
            return [abbr]
    return [abbr for abbr, (city, _) in NFL_TEAMS.items() if q == city.lower()]
