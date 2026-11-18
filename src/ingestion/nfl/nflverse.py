"""nflverse release assets (CC-BY-4.0): the only module that reads them.

Weekly rosters, depth charts, snap counts, the official injury reports and the schedule.
"""

import csv
import gzip
import io
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from storage.db import now_iso

from . import teams
from .base import FetchResult, SourceError, http_date_iso, iso
from .crosswalk import FANTASY_POSITIONS, Crosswalk

BASE = "https://github.com/nflverse/nflverse-data/releases/download"
client = httpx.Client(headers={"User-Agent": "fantasy-agent/0.1"}, timeout=120, follow_redirects=True)
EASTERN = ZoneInfo("America/New_York")
OFFENSE = {"QB", "RB", "FB", "WR", "TE", "LT", "LG", "C", "RG", "RT"}
GONE = {"CUT", "RET", "TRD"}  # roster statuses that mean "not on that NFL team now"


class NflverseSource:
    name = "nflverse"
    kinds = frozenset({"roster", "depth", "injury", "usage", "schedule"})

    def __init__(self, xwalk: Crosswalk, etags: dict[str, str]):
        """etags: asset url -> last seen ETag. Updated here; the caller stores it."""
        self.xwalk, self.etags = xwalk, etags

    def open_asset(self, release: str, filename: str, force: bool = False):
        """Any nflverse release asset as (csv rows, last-modified). None when unchanged since the last
        fetch, unless force. For other stages too, e.g. ("stats_player", "stats_player_week_2025.csv.gz").
        """
        return self._open(f"{BASE}/{release}/{filename}", force)

    def _open(self, url: str, force: bool = False):
        """(csv rows, last-modified), or None when the asset has not changed since the last fetch."""
        try:
            tag = client.head(url).headers.get("etag")
            if tag and self.etags.get(url) == tag and not force:
                return None
            r = client.get(url)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise SourceError(f"nflverse {url}: {e}") from e
        self.etags[url] = r.headers.get("etag", "")
        text = io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(r.content)), encoding="utf-8", newline="")
        return csv.DictReader(text), http_date_iso(r.headers.get("last-modified"))

    def fetch(self, kind: str, season: int, week: int | None = None) -> FetchResult | None:
        """None means the asset is unchanged."""
        return getattr(self, f"_{kind}")(season)

    def _roster(self, season: int):
        opened = self._open(f"{BASE}/weekly_rosters/roster_weekly_{season}.csv.gz")
        if not opened:
            return None
        reader, as_of = opened
        latest = {}
        for r in reader:
            if r["gsis_id"] and (r["gsis_id"] not in latest or int(r["week"]) >= int(latest[r["gsis_id"]]["week"])):
                latest[r["gsis_id"]] = r
        return self.roster_from(list(latest.values()), as_of)

    def roster_from(self, latest: list[dict], as_of: str | None) -> FetchResult:
        """latest: each player's newest weekly roster row."""
        self.xwalk.learn_rosters(latest)
        rows, total, unmapped, ts = [], 0, 0, now_iso()
        for r in latest:
            fantasy = r["position"] in FANTASY_POSITIONS
            pid = self.xwalk.resolve("nfl", r["gsis_id"], r["full_name"], r["team"], r["position"])
            total += fantasy
            if not pid:
                unmapped += fantasy
            elif r["status"] not in GONE and (team := teams.resolve(r["team"])):
                rows.append({"player_id": pid, "source": self.name, "nfl_team": team,
                             "roster_status": r["status"], "as_of": as_of, "fetched_at": ts})
        return FetchResult(rows, total, unmapped, as_of=as_of)

    def _depth(self, season: int):
        opened = self._open(f"{BASE}/depth_charts/depth_charts_{season}.csv.gz")
        if not opened:
            return None
        reader, _ = opened
        latest, dt = [], ""
        for r in reader:  # the file holds every snapshot since March; keep the newest
            if r["dt"] > dt:
                latest, dt = [], r["dt"]
            if r["dt"] == dt:
                latest.append(r)
        return self.depth_from(latest, dt)

    def depth_from(self, latest: list[dict], dt: str) -> FetchResult:
        rows, total, unmapped, ts = [], 0, 0, now_iso()
        for r in latest:
            total += 1
            pid = self.xwalk.resolve("nfl", r["gsis_id"], r["player_name"], r["team"], None)
            if not pid:
                unmapped += 1
                continue
            unit = "special" if r["pos_grp"] == "Special Teams" else "offense" if r["pos_abb"] in OFFENSE else "defense"
            rows.append({"source": self.name, "nfl_team": teams.resolve(r["team"]), "unit": unit,
                         "slot_label": f"{r['pos_abb']}{r['pos_rank']}", "rank": int(r["pos_rank"]),
                         "player_id": pid, "as_of": dt, "fetched_at": ts})
        return FetchResult(rows, total, unmapped, as_of=dt)

    def _injury(self, season: int):
        opened = self._open(f"{BASE}/injuries/injuries_{season}.csv.gz")
        if not opened:
            return None
        reader, as_of = opened
        return self.injury_from(reader, season, as_of)

    def injury_from(self, reader, season: int, as_of: str | None) -> FetchResult:
        rows, total, unmapped, ts = [], 0, 0, now_iso()
        for r in reader:
            if r["position"] not in FANTASY_POSITIONS:
                continue
            total += 1
            pid = self.xwalk.resolve("nfl", r["gsis_id"], r["full_name"], r["team"], r["position"])
            if not pid:
                unmapped += 1
                continue
            rows.append({"player_id": pid, "season": season, "week": int(r["week"]), "source": self.name,
                         "designation": r["report_status"] or None,
                         "practice_status": r["practice_status"] or None,
                         "body_part": r["report_primary_injury"] or r["practice_primary_injury"] or None,
                         "detail": r["report_secondary_injury"] or r["practice_secondary_injury"] or None,
                         "return_date": None, "raw": None, "as_of": as_of, "fetched_at": ts})
        return FetchResult(rows, total, unmapped, as_of=as_of)

    def _usage(self, season: int):
        opened = self._open(f"{BASE}/snap_counts/snap_counts_{season}.csv.gz")
        if not opened:
            return None
        reader, as_of = opened
        return self.usage_from(reader, season, as_of)

    def usage_from(self, reader, season: int, as_of: str | None) -> FetchResult:
        rows, total, unmapped, ts = [], 0, 0, now_iso()
        for r in reader:
            if r["position"] not in FANTASY_POSITIONS:
                continue
            total += 1
            pid = self.xwalk.resolve("pfr", r["pfr_player_id"], r["player"], r["team"], r["position"])
            if not pid:
                unmapped += 1
                continue
            rows.append({"player_id": pid, "season": season, "week": int(r["week"]), "source": self.name,
                         "offense_pct": float(r["offense_pct"]), "defense_pct": float(r["defense_pct"]),
                         "st_pct": float(r["st_pct"]), "targets": None, "carries": None, "target_share": None,
                         "as_of": as_of, "fetched_at": ts})
        return FetchResult(rows, total, unmapped, as_of=as_of)

    def _schedule(self, season: int):
        opened = self._open(f"{BASE}/schedules/games.csv.gz")
        if not opened:
            return None
        reader, as_of = opened
        return self.schedule_from(reader, season, as_of)

    def schedule_from(self, reader, season: int, as_of: str | None) -> FetchResult:
        rows, ts = [], now_iso()
        for r in reader:
            if int(r["season"]) != season or r["game_type"] != "REG":
                continue
            kickoff = None
            if r["gametime"]:  # US Eastern in the file
                kickoff = iso(datetime.fromisoformat(f"{r['gameday']}T{r['gametime']}").replace(tzinfo=EASTERN))
            final = r["home_score"] != ""
            rows.append({"game_id": r["game_id"], "season": season, "week": int(r["week"]),
                         "home": teams.resolve(r["home_team"]), "away": teams.resolve(r["away_team"]),
                         "kickoff_utc": kickoff, "status": "final" if final else "scheduled",
                         "home_score": int(float(r["home_score"])) if final else None,
                         "away_score": int(float(r["away_score"])) if final else None,
                         "sleeper_game_id": None,
                         "espn_event_id": str(int(float(r["espn"]))) if r["espn"] else None,
                         "source": self.name, "as_of": as_of, "fetched_at": ts})
        return FetchResult(rows, as_of=as_of)
