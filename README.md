# NFL Fantasy Engine

See `PLAN.md` for the full build plan.

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
uv run python -m src.interface.cli projections [--week N] [--season]
uv run python -m src.interface.cli lineup [--week N]
uv run python -m src.interface.cli waivers [--limit N]
uv run python -m src.interface.cli trades --give "name,name" --receive "name,name" [--season]
uv run python -m src.interface.cli draft
uv run python -m src.interface.cli news [--player "name"] [--limit N]
uv run python -m src.interface.cli trending [--drop] [--limit N]
uv run python -m src.interface.cli results [--week N]
```

Ask the agent in natural language (needs `OPENROUTER_API_KEY` in `.env`):

```
uv run python -m src.interface.cli ask "who should I start at flex?"
uv run python -m src.interface.cli chat
```

## Tests

```
uv run pytest
```
