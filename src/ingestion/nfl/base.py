"""Shared pieces of the NFL fact sources (docs/data-contract.md section 2)."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


class SourceError(Exception):
    """A source could not be read. status is the HTTP status when there was one."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class FetchResult:
    rows: list[dict]             # column dicts for the kind's table; player_id is already a Sleeper id
    total: int = 0               # source rows that should have mapped (fantasy and IDP positions)
    unmapped: int = 0            # of those, rows the crosswalk could not resolve
    gaps: list[str] = field(default_factory=list)
    as_of: str | None = None
    extra: dict = field(default_factory=dict)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_date_iso(value: str | None) -> str | None:
    return iso(parsedate_to_datetime(value)) if value else None


def espn_iso(value: str) -> str:
    """ESPN writes 2026-11-03T18:57Z (no seconds)."""
    return iso(datetime.fromisoformat(value.replace("Z", "+00:00")))
