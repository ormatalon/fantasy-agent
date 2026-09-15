# NFL Fantasy Engine

See `PLAN.md` for the full build plan. This is Stage 0: a read-only Sleeper
data spine.

## Setup

```
uv sync
cp .env.example .env   # then fill in SLEEPER_USERNAME
```

## Usage

```
uv run python -m src.interface.cli sync
uv run python -m src.interface.cli roster
uv run python -m src.interface.cli roster --team "some other team"
uv run python -m src.interface.cli transactions
uv run python -m src.interface.cli projections [--week N]
```

## Tests

```
uv run pytest
```
