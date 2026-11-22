# Eval datasets

| File | What it tests | Size | Curated |
|---|---|---|---|
| `tool_calling.json` | Right tool(s) and arguments for a question. 3 per tool (21 tools in `src/agent/tools.py`), plus multi-tool and no-tool cases | 73 | No |
| `refusal_rate.json` | Refuses when it should, answers when it can. `all` = 53 questions, `curated` = 15 reviewed | 53 | 15 of 53 |
| `adversarial.json` | False premises, ambiguous names, injection, future weeks | 27 | Yes |
| `consistency.json` | Same question, 3 phrasings, same answer | 50 groups | No |

Labels:
- `should_refuse`: `true`, `false`, or `"partial"` (does the allowed part, declines the rest).
- `expected_args` values in `<angle brackets>` are placeholders to resolve against live league data.
- Player names are examples; check them against current Sleeper data before scoring.

Tool calling and refusal are scored deterministically. Adversarial and consistency need an LLM judge.
