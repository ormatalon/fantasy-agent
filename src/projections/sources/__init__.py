"""Registry of active projection sources.

To add a new source (including a future ML model): implement `ProjectionSource`
in a new module here, add an instance to `ALL_SOURCES` below, and give it a
weight in `config.SOURCE_WEIGHTS`. Nothing in `blend.py` or any decision
module needs to change.
"""

from src.projections.sources.nflverse_src import NflverseSource
from src.projections.sources.sleeper_src import SleeperSource

ALL_SOURCES = [
    SleeperSource(),
    NflverseSource(),
]
