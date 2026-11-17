"""Projection sources (docs/data-contract.md section 6). A source returns stats; the blend scores them."""

import copy
from collections import defaultdict
from dataclasses import dataclass
from statistics import variance
from typing import Callable, Protocol

from projections import nflverse_data, sleeper_data
from projections.scoring import REFERENCE_PPR, score


@dataclass
class SourceRow:
    player_id: str  # Sleeper id
    week: int
    stats: dict[str, float]  # Sleeper stat keys, expected values
    points_variance: float | None  # in reference PPR points when the source has its own


class ProjectionSource(Protocol):
    name: str
    stat_keys: set[str]  # keys the source can project, for the unprojected-scoring-items list

    def weekly(self, season: int, weeks: list[int]) -> list[SourceRow]: ...


class SleeperSource:
    """Sleeper's weekly projection rows. A row with no stats is Sleeper projecting 0, not silence."""

    name = "sleeper"

    def __init__(self, loader: Callable[[int, int], list[dict]] = sleeper_data.weekly_projections):
        self.loader = loader
        self.stat_keys: set[str] = set()  # the keys Sleeper has projected in the rows loaded so far

    def weekly(self, season: int, weeks: list[int]) -> list[SourceRow]:
        rows = [SourceRow(r["player_id"], w, r["stats"], None) for w in weeks for r in self.loader(season, w)]
        for row in rows:
            self.stat_keys |= row.stats.keys()
        return rows


class NflverseSource:
    """Weighted mean of each stat over the player's last n_games games before the target week, across seasons.

    Games from earlier seasons count prior_weight (1.0 = as much as this season's). A player's NFL team for the
    target week is the weekly roster file's (teams), else the team of his latest game; a row is emitted only for
    weeks that NFL team plays (schedule: week -> team abbreviations of the target season).
    """

    name = "nflverse"
    stat_keys = nflverse_data.PROJECTED_KEYS
    min_games_for_variance = 3

    def __init__(self, games: list[dict], id_map: dict[str, str], schedule: dict[int, set[str]], n_games: int = 4,
                 prior_weight: float = 1.0, teams: dict[str, dict[int, str]] | None = None):  # fmt: skip
        self.id_map, self.schedule, self.n_games, self.prior_weight = id_map, schedule, n_games, prior_weight
        self.teams, self.cutoff = teams or {}, 99
        self.history = defaultdict(list)
        for g in sorted(games, key=lambda g: (g["season"], g["week"])):
            if g["player_id"] in id_map:
                g["ref_points"] = score(g["stats"], REFERENCE_PPR)
                self.history[id_map[g["player_id"]]].append(g)

    @classmethod
    def for_season(cls, season: int, n_games: int = 4, prior_weight: float = 0.5, prior_seasons: int = 1,
                   schedule: dict[int, set[str]] | None = None, max_age: float | None = None):  # fmt: skip
        """schedule: week -> NFL teams playing; default is the teams seen in that week's box scores."""
        years = range(season - prior_seasons, season + 1)
        games = [g for y in years for g in nflverse_data.player_games(y, max_age)]
        if schedule is None:
            schedule = defaultdict(set)
            for g in games:
                if g["season"] == season:
                    schedule[g["week"]].add(g["nfl_team"])
        id_map = {}
        for y in years:
            id_map |= nflverse_data.gsis_to_sleeper(y, max_age)
        return cls(games, id_map, schedule, n_games, prior_weight, nflverse_data.roster_teams(season, max_age))

    def with_options(self, n_games: int | None = None, prior_weight: float | None = None, cutoff: int | None = None):
        """A view of the same history with other options; cutoff = knows only games before that week."""
        clone = copy.copy(self)
        clone.n_games = n_games or self.n_games
        clone.prior_weight = self.prior_weight if prior_weight is None else prior_weight
        clone.cutoff = cutoff or self.cutoff
        return clone

    def recent(self, player_id: str, season: int, week: int) -> list[dict]:
        before = (season, min(week, self.cutoff))
        return [g for g in self.history.get(player_id, []) if (g["season"], g["week"]) < before][-self.n_games :]

    def team_at(self, player_id: str, week: int, recent: list[dict]) -> str | None:
        known = [w for w in self.teams.get(player_id, {}) if w <= week]
        return self.teams[player_id][max(known)] if known else (recent[-1]["nfl_team"] if recent else None)

    def weekly(self, season: int, weeks: list[int]) -> list[SourceRow]:
        out = []
        for player_id in self.history:
            for w in weeks:
                recent = self.recent(player_id, season, w)
                if not recent or self.team_at(player_id, w, recent) not in self.schedule.get(w, ()):
                    continue
                wt = [self.prior_weight if g["season"] < season else 1.0 for g in recent]
                stats = {k: sum(x * g["stats"][k] for x, g in zip(wt, recent)) / sum(wt) for k in recent[0]["stats"]}
                var = variance([g["ref_points"] for g in recent]) if len(recent) >= self.min_games_for_variance else None
                out.append(SourceRow(player_id, w, stats, var))
        return out
