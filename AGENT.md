## Start here

`PRD.md` is the spec: what to build, what the first build learned, and what
is still open. Read it before writing code. "First build:" notes in it are
guidance, not requirements. `TEST_QUESTIONS.md` holds the live checks for the
agent's behavior; extend it as features land.

## Agent team ownership

- ai-engineer: src/agent
- data-scientist: src/projections, src/decisions, src/evaluation, notebooks/
- backend-developer: src/web (FastAPI app and API), src/ingestion, src/storage,
  pyproject.toml, scripts/, desktop/, Dockerfile, docker-compose.yml,
  .github/workflows/, .env.example
- ui-designer: design/ (spec and mockups only, no code)
- frontend-developer: src/ui (NiceGUI)
- qa-expert: tests/ (live checks and acceptance; files bugs as tasks to the
  owner, doesn't fix code)
Unit tests and the fixtures they use live in the owner's folder
(`src/<area>/tests/`); anyone may read another folder's fixtures.
Never edit another role's folder. Changes to a shared contract go through a message to the affected roles first.

## Terminology: two kinds of team

This project has exactly two kinds of team. Never write a bare "team" where it
could mean either: in code, docs, prompts, tool names, CLI flags, UI text or
answers.

- **Fantasy team**: a manager's roster in a Sleeper league (mine, or another
  manager's). Use `fantasy_team` in names. Sleeper's own field
  `league_users.team_name` holds a fantasy team's name.
- **NFL team**: a real NFL franchise (SEA, "Seattle", "Seahawks"). Use
  `nfl_team` in names. A player's NFL team in Sleeper is `players.team` (an
  abbreviation).

The agent's system prompt must give the model the same rule. Keep the two in
sync.

## Rules for the product's agent

These come from the PRD and are not open to change:

- **Only what it was given.** Every fact, reason and prediction the agent
  states comes from information the product supplied (tool results, prompt
  context, the user's words), never from the model's training knowledge. A
  gap is stated as a gap.
- **Tools compute.** No arithmetic in model text. If the model needs a number,
  a tool provides it.
- **Facts, not prompt rules.** When the model gets something wrong, first give
  it the fact (a tool or prompt context); add a prompt rule only as a last
  resort.
- **Confirmed moves only.** Nothing is written to Sleeper until the manager
  confirms that exact change. The check lives in code the model can't reach,
  and tests try to get around it.

## Technical decisions

- NiceGUI frontend (`src/ui`), in the same Python process as the FastAPI
  backend: one app serves the UI at `/` and the API at `/api`, and the UI
  calls the backend modules directly.
- Everything packaged into a Docker container, with uv as the Python package
  manager.
- Start and stop scripts in `scripts/` as docker compose wrappers (Mac/Linux:
  `.sh`, Windows: `.bat`).
- Desktop (Windows, Mac) and Android apps per the PRD. Desktop reuses the
  NiceGUI app in a native window. Android is not decided: NiceGUI renders
  from a Python server, so the phone needs a server to reach.
- OpenRouter for model calls, with `OPENROUTER_API_KEY` from `.env` in the
  project root (users enter their own key in the app). Use a free model that
  reliably calls tools, pinned by id, not an auto-router. Last known good:
  `nvidia/nemotron-3-ultra-550b-a55b:free`; check it is still free and works
  before relying on it.
- SQLite local database, created if it doesn't exist.
- Secrets only in env or local settings, never committed. Keep `.env.example`
  current.

## Color scheme

- Accent Yellow: `#ecad0a` - accent lines, highlights
- Blue Primary: `#209dd7` - links, key sections
- Purple Secondary: `#753991` - submit buttons, important actions (including
  confirming a move)
- Dark Navy: `#032147` - main headings
- Gray Text: `#888888` - supporting text, labels

## How to work

- Build in stages. At the end of each stage: stop, say what was built, give
  exact commands to test it, and wait for the owner's approval. Commit only
  after approval, one stage per commit.
- Read-only features before writes. Writes go to a test league first.
- A projection change ships only if the backtest beats the current baseline in
  two independent seasons.
- When hitting issues, always identify the root cause before trying a fix. Do
  not guess. Prove it with evidence, then fix the root cause.
- When a decision in the PRD turns out wrong, say so and update the PRD in the
  same change.

## Coding standards

- Use the latest versions of libraries and idiomatic approaches as of today.
- Keep it simple. NEVER over-engineer, ALWAYS simplify, NO unnecessary
  defensive programming. No extra features: focus on simplicity.
- Be concise. Keep README minimal. IMPORTANT: no emojis ever.
- Each undocumented API (Sleeper GraphQL, others) is called from one module
  only.
- Tests use real fixtures (actual API responses, real headlines) where
  possible.

## Coding in notebooks

Applies to the planned own projection model and any analysis.

- A notebook can be Jupyter, Databricks or Google Colab. If unsure which, ask.
- Follow the coding standards above.
- Short titles and documentation; keep cells short, and print relevant info or
  data at the end of a cell where possible.
- Charts have titles and axis labels. Review each figure and fix axis limits
  or use a log scale when needed so it reads clearly.
- Charts in matplotlib or seaborn, except time series, which use plotly for
  interactivity.

## Model training pipeline

- Train in notebooks, following the notebook rules.
- A training notebook includes data loading, preprocessing, training with
  MLflow tracking, and a simple evaluation.
- The model plugs into the product as a projection source and must pass the
  backtest gate above.

## Retraining pipeline

- Follow the structure of the Databricks MLOps stack:
  https://github.com/databricks/mlops-stacks/ (see
  `` template/{{.input_root_dir}}/{{template `project_name_alphanumeric_underscore` .}} ``).
- Ask the owner: the project name; whether the code goes in this repo or a new
  one; whether to use a feature store.
- Inference can be batch or streaming.
- One change from the template: inference includes data loading,
  preprocessing, inference and post-processing if needed.
