"""nflverse release files used by the projection source: per-game box scores and the Sleeper id column.

Backtest files are downloaded once and kept under the app data folder. Downloads go through
ingestion.nfl.nflverse's open_asset.
"""

import csv
import gzip
import io
import time
from pathlib import Path

from ingestion.nfl import nflverse
from ingestion.nfl.base import SourceError
from storage.db import data_dir

# nflverse stats_player_week column -> Sleeper stat key
STAT_COLUMNS = {
    "passing_yards": "pass_yd", "passing_tds": "pass_td", "passing_interceptions": "pass_int",
    "passing_2pt_conversions": "pass_2pt", "carries": "rush_att", "rushing_yards": "rush_yd",
    "rushing_tds": "rush_td", "rushing_2pt_conversions": "rush_2pt", "receptions": "rec",
    "targets": "rec_tgt", "receiving_yards": "rec_yd", "receiving_tds": "rec_td",
    "receiving_2pt_conversions": "rec_2pt", "special_teams_tds": "st_td",
    "def_tackles_solo": "idp_tkl_solo", "def_tackle_assists": "idp_tkl_ast",
    "def_tackles_for_loss": "idp_tkl_loss", "def_sacks": "idp_sack", "def_qb_hits": "idp_qb_hit",
    "def_interceptions": "idp_int", "def_pass_defended": "idp_pass_def", "def_fumbles_forced": "idp_ff",
    "fumble_recovery_opp": "idp_fum_rec", "def_tds": "idp_def_td", "def_safeties": "idp_safe",
    "fg_made_0_19": "fgm_0_19", "fg_made_20_29": "fgm_20_29", "fg_made_30_39": "fgm_30_39",
    "fg_made_40_49": "fgm_40_49", "fg_made_50_59": "fgm_50_59", "fg_made_60_": "fgm_60p",
    "fg_missed": "fgmiss", "pat_made": "xpm", "pat_missed": "xpmiss",
}  # fmt: skip
FUMBLES_LOST = ("sack_fumbles_lost", "rushing_fumbles_lost", "receiving_fumbles_lost")
NFL_TEAM = {"LA": "LAR"}  # nflverse code -> Sleeper abbreviation (the only difference)
PROJECTED_KEYS = set(STAT_COLUMNS.values()) | {"fum_lost"}


def _download(dataset: str, name: str, max_age: float | None = None) -> bytes:
    """Cached forever for past seasons; the current season passes max_age (seconds). Returns the csv bytes."""
    path = data_dir() / "backtest_cache" / "nflverse" / name
    if not path.exists() or (max_age is not None and time.time() - path.stat().st_mtime > max_age):
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            reader, _ = nflverse.NflverseSource(None, {}).open_asset(dataset, name, force=True)
        except SourceError:  # older seasons are published uncompressed only; open_asset reads gzip only
            raw = nflverse.client.get(f"{nflverse.BASE}/{dataset}/{name.removesuffix('.gz')}")
            raw.raise_for_status()
            path.write_bytes(gzip.compress(raw.content))
        else:
            out = io.StringIO()
            writer = csv.DictWriter(out, fieldnames=reader.fieldnames)
            writer.writeheader()
            writer.writerows(reader)
            path.write_bytes(gzip.compress(out.getvalue().encode("utf-8")))
    return gzip.decompress(path.read_bytes())


def _rows(raw: bytes) -> csv.DictReader:
    return csv.DictReader(io.StringIO(raw.decode("utf-8")))


def _num(x: str) -> float:
    return float(x) if x else 0.0


def games_from_csv(raw: bytes) -> list[dict]:
    """Regular-season games played: gsis_id (as player_id), week, nfl_team (Sleeper abbreviation), stats (Sleeper keys)."""
    out = []
    for r in _rows(raw):
        if r["season_type"] != "REG":
            continue
        stats = {sk: _num(r[col]) for col, sk in STAT_COLUMNS.items()}
        stats["fum_lost"] = sum(_num(r[c]) for c in FUMBLES_LOST)
        out.append(
            {"player_id": r["player_id"], "name": r["player_display_name"], "position": r["position"],
             "season": int(r["season"]), "week": int(r["week"]), "nfl_team": NFL_TEAM.get(r["team"], r["team"]), "stats": stats}
        )  # fmt: skip
    return out


def id_map_from_csv(raw: bytes) -> dict[str, str]:
    """gsis_id -> Sleeper id from the weekly roster file's sleeper_id column (the PRD's primary bridge)."""
    return {r["gsis_id"]: r["sleeper_id"] for r in _rows(raw) if r["gsis_id"] and r["sleeper_id"]}


def player_games(season: int, max_age: float | None = None) -> list[dict]:
    return games_from_csv(_download("stats_player", f"stats_player_week_{season}.csv.gz", max_age))


def gsis_to_sleeper(season: int, max_age: float | None = None) -> dict[str, str]:
    return id_map_from_csv(_download("weekly_rosters", f"roster_weekly_{season}.csv.gz", max_age))


def roster_info(season: int) -> dict[str, dict]:
    """Sleeper id -> pfr_id and years_exp (0 = rookie) from the weekly roster file; for analysis."""
    return {
        r["sleeper_id"]: {"pfr_id": r["pfr_id"], "years_exp": int(r["years_exp"] or 0)}
        for r in _rows(_download("weekly_rosters", f"roster_weekly_{season}.csv.gz")) if r["sleeper_id"]
    }  # fmt: skip


def snap_shares(season: int) -> dict[str, dict[int, float]]:
    """pfr_player_id -> week -> share of the NFL team's offensive snaps (0-1); for analysis."""
    out: dict[str, dict[int, float]] = {}
    for r in _rows(_download("snap_counts", f"snap_counts_{season}.csv.gz")):
        if r["game_type"] == "REG" and r["offense_pct"]:
            out.setdefault(r["pfr_player_id"], {})[int(r["week"])] = float(r["offense_pct"])
    return out


def roster_teams(season: int, max_age: float | None = None) -> dict[str, dict[int, str]]:
    """Sleeper id -> week -> NFL team (Sleeper abbreviation) from the weekly roster file."""
    out: dict[str, dict[int, str]] = {}
    for r in _rows(_download("weekly_rosters", f"roster_weekly_{season}.csv.gz", max_age)):
        if r["sleeper_id"] and r["game_type"] == "REG":
            out.setdefault(r["sleeper_id"], {})[int(r["week"])] = NFL_TEAM.get(r["team"], r["team"])
    return out
