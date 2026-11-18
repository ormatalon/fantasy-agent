"""Freeze the NFL clock: the captures are from 2026-11-17 and week 11 kicks off 2026-11-20."""

from datetime import UTC, datetime

import pytest

from ingestion.nfl import service

NOW = datetime(2026, 11, 17, 12, tzinfo=UTC)


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.astimezone().replace(tzinfo=None)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    monkeypatch.setattr(service, "datetime", FrozenDatetime)
