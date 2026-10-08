## Terminology: two kinds of team

This project has exactly two kinds of team. Never write a bare "team" where it could mean either: in code, docs, prompts, tool names, CLI flags or answers.

- **Fantasy team**: a manager's roster in a Sleeper league (mine, or another manager's). Code: `fantasy_team_*` names, `storage.fantasy_team_label`, `storage.find_roster_by_fantasy_team`, `AgentContext.fantasy_team_name`, tool `get_fantasy_team_roster`, CLI `roster --fantasy-team`. Sleeper's own field `league_users.team_name` holds a fantasy team's name.
- **NFL team**: a real NFL franchise (SEA, "Seattle", "Seahawks"). Code: `src/ingestion/nfl_teams.py` (`resolve_nfl_team`, `nfl_team_label`), `storage.players_on_nfl_team`, tool `get_nfl_team_roster`. A player's NFL team is `players.team` (an abbreviation).

The agent's system prompt (`src/agent/prompts.py`, TERMINOLOGY) gives the model the same rule. Keep the two in sync.

## Technical Decisions if Creating an app

- use NextJS frontend
- If needed Python FastAPI backend, including serving the static NextJS site at /
- Everything packaged into a Docker container
- Use "uv" as the package manager for python in the Docker container
- Use OpenRouter for the AI calls. An OPENROUTER_API_KEY is in .env in the project root
- Use `openai/gpt-oss-120b:free` as the model
- Use SQLLite local database for the database, creating a new db if it doesn't exist
- Start and stop scripts in scripts/ as docker compose wrappers (Mac/Linux: .sh, Windows: .bat)

## Color Scheme

- Accent Yellow: `#ecad0a` - accent lines, highlights
- Blue Primary: `#209dd7` - links, key sections
- Purple Secondary: `#753991` - submit buttons, important actions
- Dark Navy: `#032147` - main headings
- Gray Text: `#888888` - supporting text, labels

## Coding standards

- Use latest versions of libraries and idiomatic approaches as of today
- Keep it simple - NEVER over-engineer, ALWAYS simplify, NO unnecessary defensive programming. No extra features - focus on simplicity.
- Be concise. Keep README minimal. IMPORTANT: no emojis ever
- When hitting issues, always identify root cause before trying a fix. Do not guess. Prove with evidence, then fix the root cause.

## Coding in notebook

- A notebook can be jupyter, databricks notebook, google colab.
- If you are unsure which notebook type, ask the user.
- Keep it simple - NEVER over-engineer, ALWAYS simplify, NO unnecessary defensive programming. No extra features - focus on simplicity.
- Make titles and documentation, but keep it simple and concise. Keep README minimal. IMPORTANT: no emojis ever.
- Keep cells short and if possible print relevant info/data at the end of the cell.
- When creating charts, have titles, axes labels, review the figure and if needed correct axes limits or log scale to make it look explainabe for human eye.
- All charts should be done with matplotlib or seaborn, except timeseries plots that should be done in plotly for interactivity.

## Building model training pipeline

- Create training in notebooks. Follow Coding in notebook rules.
- Training notebook should include: data loading, preprocessing, training model including tracking with MLFlow, and simple evaluation.

## Create retraining pipeline

- Follow the structure of Databricks MLOPS stack: [https://github.com/databricks/mlops-stacks/](https://github.com/databricks/mlops-stacks/)
- In the repo take example from template/{{.input_root_dir}}/{{template `project_name_alphanumeric_underscore` .}} to know how it's structured.
- Ask the user how to name the project.
- Ask the user where the code should be. As part of an existing repo or as a new repo.
- Ask the user whether to use feature store or not.
- Inference can be in batch or in streaming.
- One change should be from the template. The inference should have data loading, preprocessing, inference and post-processing if needed.

