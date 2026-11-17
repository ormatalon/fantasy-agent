"""ESPN public JSON (undocumented, no published terms): the only module that calls it.

Injuries, scoreboard (kickoff and game state), game-day inactives and NFL team rosters.
A 403 or 429 means we are being throttled; SourceError.status carries it so the caller backs off.
"""

import re

import httpx

from storage.db import now_iso

from . import teams
from .base import FetchResult, SourceError, espn_iso
from .crosswalk import FANTASY_POSITIONS, Crosswalk

SITE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
client = httpx.Client(headers={"User-Agent": "fantasy-agent/0.1"}, timeout=30, follow_redirects=True)

DESIGNATION = {"Injured Reserve": "IR", "Suspension": "Sus"}  # ESPN's words -> Sleeper's
ESPN_POSITION = {"PK": "K"}
GAME_STATE = {"pre": "scheduled", "in": "in_progress", "post": "final"}


def _get(url: str, **params):
    try:
        r = client.get(url, params=params)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        raise SourceError(f"ESPN {url}: {e}", e.response.status_code) from e
    except httpx.HTTPError as e:
        raise SourceError(f"ESPN {url}: {e}") from e


def _athlete_id(athlete: dict) -> str | None:
    """The injuries list has no athlete id field; it is in the player-card link (.../id/4870808/...)."""
    for link in athlete["links"]:
        if m := re.search(r"/id/(\d+)/", link["href"]):
            return m.group(1)
    return None


class EspnSource:
    name = "espn"
    kinds = frozenset({"injury", "schedule", "inactive", "roster"})

    def __init__(self, xwalk: Crosswalk):
        self.xwalk = xwalk

    def fetch(self, kind: str, season: int, week: int, **kw) -> FetchResult:
        """kw: team (Sleeper abbreviation) for roster; games (nfl_game rows) for inactive."""
        if kind == "injury":
            return self.injury_from(_get(f"{SITE}/injuries"), season, week)
        if kind == "schedule":
            return self.schedule_from([_get(f"{SITE}/scoreboard", week=w, seasontype=2) for w in (week - 1, week) if w >= 1], season)
        if kind == "roster":
            return self.roster_from(_get(f"{SITE}/teams/{teams.BY_ABBR[kw['team']]['espn_id']}/roster"))
        return self.inactive_from([(g, self._entries(g)) for g in kw["games"]])

    def injury_from(self, payload: dict, season: int, week: int) -> FetchResult:
        rows, total, unmapped, ts = [], 0, 0, now_iso()
        for group in payload["injuries"]:
            for item in group["injuries"]:
                if item["status"] == "Active":
                    continue
                a = item["athlete"]
                pos = ESPN_POSITION.get(a["position"]["abbreviation"], a["position"]["abbreviation"])
                if pos not in FANTASY_POSITIONS:
                    continue
                total += 1
                team = teams.resolve(a["team"]["abbreviation"])
                pid = self.xwalk.resolve("espn", _athlete_id(a), a["displayName"], team, pos)
                if not pid:
                    unmapped += 1
                    continue
                d = item.get("details") or {}
                as_of = espn_iso(item["date"])
                rows.append({"player_id": pid, "season": season, "week": week, "source": self.name,
                             "designation": DESIGNATION.get(item["status"], item["status"]),
                             "practice_status": None, "body_part": d.get("type"), "detail": d.get("detail"),
                             "return_date": d.get("returnDate"), "raw": item.get("shortComment"),
                             "as_of": as_of, "fetched_at": ts})
        return FetchResult(rows, total, unmapped, as_of=ts)

    def schedule_from(self, payloads: list[dict], season: int) -> FetchResult:
        rows, byes, ts = [], {}, now_iso()
        for payload in payloads:
            for t in payload["week"].get("teamsOnBye", []):
                byes[teams.resolve(t["abbreviation"])] = payload["week"]["number"]
            for e in payload["events"]:
                comp = e["competitions"][0]
                side = {c["homeAway"]: c for c in comp["competitors"]}
                status = GAME_STATE[e["status"]["type"]["state"]]
                rows.append({"season": season, "week": payload["week"]["number"],
                             "home": teams.resolve(side["home"]["team"]["abbreviation"]),
                             "away": teams.resolve(side["away"]["team"]["abbreviation"]),
                             "kickoff_utc": espn_iso(e["date"]), "status": status,
                             "home_score": int(side["home"]["score"]) if status != "scheduled" else None,
                             "away_score": int(side["away"]["score"]) if status != "scheduled" else None,
                             "espn_event_id": e["id"], "as_of": ts})
        return FetchResult(rows, as_of=ts, extra={"byes": byes})

    def _entries(self, game: dict) -> dict[str, list[dict]]:
        """Roster entries per ESPN team id for one game."""
        event = game["espn_event_id"]
        return {
            teams.BY_ABBR[t]["espn_id"]: _get(f"{CORE}/events/{event}/competitions/{event}/competitors/"
                                              f"{teams.BY_ABBR[t]['espn_id']}/roster")["entries"]
            for t in (game["home"], game["away"])
        }

    def inactive_from(self, games: list[tuple[dict, dict[str, list[dict]]]]) -> FetchResult:
        """ESPN marks a player didNotPlay once inactives are known. None marked = not announced yet."""
        rows, gaps, total, unmapped, ts = [], [], 0, 0, now_iso()
        for game, by_team in games:
            out = [e for entries in by_team.values() for e in entries if e["didNotPlay"]]
            if not out:
                gaps.append(f"espn: no inactives listed yet for {game['game_id']}")
                continue
            for e in out:
                total += 1
                pid = self.xwalk.resolve("espn", e["playerId"])
                if not pid:
                    unmapped += 1
                    continue
                rows.append({"game_id": game["game_id"], "player_id": pid, "source": self.name,
                             "as_of": ts, "fetched_at": ts})
        return FetchResult(rows, total, unmapped, gaps, as_of=ts)

    def roster_from(self, payload: dict) -> FetchResult:
        team = teams.resolve(payload["team"]["abbreviation"])
        rows, total, unmapped, ts = [], 0, 0, now_iso()
        for group in payload["athletes"]:
            for a in group["items"]:
                pos = ESPN_POSITION.get(a["position"]["abbreviation"], a["position"]["abbreviation"])
                total += pos in FANTASY_POSITIONS
                pid = self.xwalk.resolve("espn", a["id"], a["fullName"], team, pos)
                if not pid:
                    unmapped += pos in FANTASY_POSITIONS
                    continue
                rows.append({"player_id": pid, "source": self.name, "nfl_team": team,
                             "roster_status": group["position"], "as_of": ts, "fetched_at": ts})
        return FetchResult(rows, total, unmapped, as_of=ts)
