"""Pluggable interface every projection source must implement."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.ingestion.id_crosswalk import Crosswalk


@dataclass(frozen=True)
class SourceProjection:
    """One player's raw point estimate from a single source, for one week.

    `player_id` is always a Sleeper player_id — each source crosswalks its
    own native ID to Sleeper's before returning here, so nothing downstream
    (blend, matchup, injury, decisions) needs to know source-specific ID
    schemes.
    """

    player_id: str
    points: float


class ProjectionSource(ABC):
    """A pluggable weekly projection feed."""

    name: str

    @abstractmethod
    def fetch(
        self,
        season: str,
        week: int,
        scoring_settings: dict[str, float],
        crosswalk: Crosswalk,
    ) -> list[SourceProjection]:
        """Return this source's point estimate for every player it covers.

        `scoring_settings` is the league's Sleeper-format scoring dict (stat
        key -> point multiplier), so every source computes fantasy points
        under the league's actual rules rather than an assumed PPR/standard
        preset. `crosswalk` resolves this source's native player IDs to
        Sleeper's — sources that are already Sleeper-native can ignore it.
        """
        raise NotImplementedError
