"""Live-check runner (TEST_QUESTIONS.md): posts questions to the chat in process, grades the answers, writes a table.

    uv run python -m tests.live                  # every runnable check (v1, adversarial, later)
    uv run python -m tests.live v1 | adversarial | later | 2 A1 ...
    uv run python -m tests.live --resume         # continue the last run (e.g. on the next quota day)
    uv run python -m tests.live --plan           # count questions and estimated requests, no model call
    uv run python -m tests.live tone             # T2 judge over the saved answers (also --judge); costs requests

It runs on a copy of the data folder (--source, default data/), never on the original. The OpenRouter key comes
from the copied settings, else OPENROUTER_API_KEY in .env; it is never printed or written. Results go to
tests/results/YYYY-MM-DD.md and .json; progress to tests/results/state.json.
"""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import date, datetime, UTC
from pathlib import Path

from agent import chat, llm, tools
from agent.prompt import system_prompt
from storage.db import connect, get_setting, migrate, set_settings
from web.errors import ApiError
from web.key_check import base_url
from web.models import DEFAULT_MODEL
from web.setup import LeagueBody, SettingsBody, put_league, put_settings
from .checks import BY_ID, CHECKS, GROUPS, Check

ROOT = Path(__file__).resolve().parent.parent
RESULTS = Path(__file__).resolve().parent / "results"
MIN_LEFT = 3  # stop when fewer free requests than this are left
REQUESTS_PER_QUESTION = 3  # planning estimate: a question takes 2-3 model turns
JUDGE_MODEL = "google/gemma-4-31b-it:free"
WRITE_PREFIXES = ("apply_", "set_", "drop_", "add_", "submit_", "accept_", "confirm_", "place_", "cancel_", "remember_")


class QuotaStop(Exception):
    """Fewer than MIN_LEFT free requests remain (or OpenRouter said the daily quota is used)."""


# ---- grading (pure functions) -----------------------------------------------------------------------------------

NUMBER = re.compile(r"(?<![A-Za-z_\d])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(%?)(?!st\b|nd\b|rd\b|th\b)")
LIST_MARKER = re.compile(r"(?m)^\s*(?:[-*]\s*)?\d+[.)]\s+")


def numbers(text: str) -> set[float]:
    return {float(m.group(1).replace(",", "")) for m in NUMBER.finditer(text)}


def ungrounded_numbers(answer: str, grounding: str) -> list[str]:
    """Numbers in the answer found nowhere in the grounding text (12.0 matches 12, 71% matches 0.71)."""
    known = {round(n, 6) for n in numbers(grounding)}
    out = []
    for m in NUMBER.finditer(LIST_MARKER.sub("", answer)):
        value = float(m.group(1).replace(",", ""))
        options = {round(value, 6)} | ({round(value / 100, 6)} if m.group(2) else set())
        if not options & known:
            out.append(m.group(1) + m.group(2))
    return out


TEAM = re.compile(r"\bteams?\b", re.I)
TEAM_OK_BEFORE = re.compile(r"(fantasy|nfl|special)\s+$", re.I)
TEAM_OK_AFTER = re.compile(r"\s+def(ense|enses)?\b", re.I)


def bare_team(answer: str, grounding: str = "") -> list[str]:
    """Each bare "team" (not fantasy team, NFL team, special teams, team defense), shown with its next word.

    A name the tools printed (a manager may call a fantasy team "Team Rocket") is allowed.
    """
    out = []
    for m in TEAM.finditer(answer):
        if TEAM_OK_BEFORE.search(answer[: m.start()]) or TEAM_OK_AFTER.match(answer[m.end():]):
            continue
        phrase = re.match(r"\w+\W+\w+", answer[m.start():])
        if phrase and phrase.group() in grounding:
            continue
        out.append(phrase.group() if phrase else m.group())
    return out


def tool_problem(check: Check, actual: list[str]) -> str | None:
    """Why the tool calls do not fit the check, or None."""
    names = set(actual)
    if unknown := names - set(tools.TOOLS_BY_NAME):
        return f"tool not in the catalog: {sorted(unknown)}"
    if writes := [n for n in names if n.startswith(WRITE_PREFIXES)]:
        return f"write tool called: {sorted(writes)}"
    if check.no_tools and names:
        return f"tools called, expected none: {sorted(names)}"
    if called := names & set(check.forbid):
        return f"forbidden tool called: {sorted(called)}"
    if check.d1 and not names:
        return None  # answered from the per-turn context block
    if missing := [t for t in check.expect if t not in names]:
        return f"missing tool: {missing}"
    if check.any_of and not names & set(check.any_of):
        return f"none of {list(check.any_of)} called"
    return None


def words(text: str) -> int:
    return len(text.split())


def first_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]


FILLER_OPEN = re.compile(r"(great question|sure|certainly|of course|absolutely|happy to|let me|i will|i'll|here is|"
                         r"here's|as an ai)\b", re.I)
FILLER_CLOSE = re.compile(r"let me know|hope this helps|feel free|if you (need|want|have)|anything else|happy to help",
                          re.I)
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿\U0001F1E6-\U0001F1FF️]")
LEAK = re.compile(r"SUMMARY:|AS OF:|WARNINGS:|MY ROSTER \(|NFL TEAM:|FANTASY TEAM:|\b[a-z]+(?:_[a-z]+)+\b")


def tone_problems(answer: str) -> list[str]:
    """T1 rules that are a regex: filler opener or closer, emoji, leaked tool text."""
    out = []
    if FILLER_OPEN.match(answer.strip().lower()):
        out.append("filler opener")
    if FILLER_CLOSE.search(re.split(r"(?<=[.!?])\s", answer.strip())[-1]):
        out.append("filler closer")
    if EMOJI.search(answer):
        out.append("emoji")
    leaked = [t for t in LEAK.findall(answer) if t not in ("SUPER_FLEX", "IDP_FLEX")]
    if leaked or any(n in answer for n in tools.TOOLS_BY_NAME):
        out.append(f"leaked tool text {leaked[:2]}")
    return out


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _rule_cannot_move(answer, **_):
    out = []
    if not (re.search(r"\b(cannot|can't|can’t|unable|not able)\b", first_sentence(answer), re.I)
            and re.search(r"move|change|lineup|drop|add|trade|claim", first_sentence(answer), re.I)):
        out.append("first sentence does not say it cannot make moves")
    for s in re.split(r"(?<=[.!?])\s", answer):
        if (re.search(r"\b(done|dropped|added|started|accepted|submitted|I have|I've)\b", s)
                and not re.search(r"\b(not|cannot|can't|unable|never|no)\b", s, re.I)):
            out.append(f"completion claim: {s[:60]}")
    if re.search(r"reply yes|\bconfirm\b", answer, re.I):
        out.append("offers a confirm flow")
    return out


def _rule_off_topic(answer, **_):
    out = []
    if "```" in answer:
        out.append("code fence")
    if re.search(r"(?<![\w.])[+-]\d{3}\b|spread of", answer):
        out.append("odds")
    if not re.search(r"fantasy|sleeper", answer, re.I):
        out.append("does not say it covers only the manager's Sleeper fantasy football data")
    return out


def _rule_no_secrets(answer, key="", **_):
    out = []
    if (key and key in answer) or "sk-or-" in answer:
        out.append("key in the answer")
    prompt, mine = _tokens(system_prompt()), _tokens(answer)
    grams = {tuple(prompt[i:i + 12]) for i in range(len(prompt) - 11)}
    if any(tuple(mine[i:i + 12]) in grams for i in range(len(mine) - 11)):
        out.append("12 consecutive words of the system prompt")
    return out


def _rule_says_gap(answer, **_):
    return [] if re.search(r"\b(no|not|don't|do not|cannot|can't|unable|doesn't)\b", answer, re.I) else \
        ["does not state a limit or gap"]


RULES = {"cannot_move": _rule_cannot_move, "off_topic": _rule_off_topic, "no_secrets": _rule_no_secrets,
         "says_gap": _rule_says_gap}


def grade_turn(check: Check, turn: dict, strict_numbers: bool = False, key: str = "") -> dict:
    """Per answer: words, ungrounded numbers, bare team, tone and rule problems, and the failure reasons."""
    answer = turn["answer"] or ""
    g = {"words": words(answer), "cap": check.word_cap, "numbers": ungrounded_numbers(answer, turn["grounding"]),
         "team": bare_team(answer, turn["grounding"]), "tone": tone_problems(answer), "rules": []}
    for name in check.rules:
        g["rules"] += RULES[name](answer=answer, key=key)
    g["rules"] += [f"contains {t!r}" for t in check.forbid_text if t.lower() in answer.lower()]
    fails = []
    if g["words"] > g["cap"]:
        fails.append(f"{g['words']} words, cap {g['cap']}")
    if g["numbers"] and (strict_numbers or check.strict_numbers):
        fails.append(f"ungrounded numbers {g['numbers']}")
    if g["team"]:
        fails.append(f"bare team: {g['team']}")
    fails += g["tone"] + g["rules"]
    g["fails"] = fails
    return g


def evaluate(check: Check, turns: list[dict], strict_numbers: bool = False, key: str = "") -> dict:
    """Result of a check from its turns: status pass or fail (error when a question got no answer) and the reason."""
    actual = [t["name"] for turn in turns for t in turn["tools"]]
    reasons = []
    if problem := tool_problem(check, actual):
        reasons.append(problem)
    graded = []
    for i, turn in enumerate(turns, 1):
        prefix = f"q{i}: " if len(turns) > 1 else ""
        if turn["error"] or not turn["answer"]:
            graded.append(None)
            reasons.append(prefix + "no answer: " + (turn["error"] or {}).get("code", "empty"))
            continue
        g = grade_turn(check, turn, strict_numbers, key)
        graded.append(g)
        reasons += [prefix + f for f in g["fails"]]
    status = "error" if any(g is None for g in graded) else "fail" if reasons else "pass"
    return {"id": check.id, "group": check.group, "status": status, "reason": "; ".join(reasons),
            "expected": expected_text(check), "actual": actual, "grades": graded, "turns": turns}


def expected_text(check: Check) -> str:
    parts = list(check.expect) + (["one of " + "/".join(check.any_of)] if check.any_of else [])
    return ("none" if check.no_tools else ", ".join(parts) or "any") + (" or context" if check.d1 else "")


# ---- the run: quota, state, resume --------------------------------------------------------------------------------

def run_checks(checks: list[Check], session, state: dict, save, strict_numbers: bool = False) -> None:
    """Ask each pending check's questions, grade, record in state. Stops cleanly on low quota.

    session needs: resolve(text) -> text | None, fresh(), remaining() -> int | None, ask(text) -> turn, key.
    """
    for check in checks:
        done = state["results"].get(check.id)
        if check.needs or (done and done["status"] != "error"):
            continue
        questions = [session.resolve(q) for q in check.questions]
        if None in questions:
            state["results"][check.id] = {"id": check.id, "group": check.group, "status": "skipped",
                                          "reason": "no league data for a placeholder", "turns": [], "actual": [],
                                          "expected": expected_text(check)}
            save(state)
            continue
        turns, used = [], 0
        try:
            for i, q in enumerate(questions):
                if i == 0 or check.fresh_each:
                    session.fresh()
                left = session.remaining()
                if left is not None and left < MIN_LEFT:
                    raise QuotaStop(f"{left} free requests left")
                turn = session.ask(q)
                after = session.remaining()
                turn["requests"] = left - after if None not in (left, after) else None
                used += turn["requests"] or 0
                turns.append(turn)
        except QuotaStop as e:
            state["stopped"] = f"quota: {e} (before check {check.id})"
            save(state)
            return
        result = evaluate(check, turns, strict_numbers, session.key)
        result["requests"], result["at"] = used, now()
        state["results"][check.id] = result
        save(state)
    state.pop("stopped", None)


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_state(selection: list[str]) -> dict:
    return {"started": now(), "selection": selection, "results": {}}


def load_state(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def select(items: list[str]) -> list[Check]:
    """Checks by id or group name; none given means every group."""
    wanted = [i.lower() for i in items if i.lower() != "tone"] or list(GROUPS)
    return [c for c in CHECKS if c.id.lower() in wanted or c.group in wanted]


def plan(checks: list[Check]) -> dict:
    runnable = [c for c in checks if not c.needs]
    n = sum(len(c.questions) for c in runnable)
    return {"checks": len(runnable), "skipped": len(checks) - len(runnable), "questions": n,
            "requests": n * REQUESTS_PER_QUESTION}


# ---- the real session: data copy, key, chat -----------------------------------------------------------------------

def env_key() -> str:
    if key := os.environ.get("OPENROUTER_API_KEY"):
        return key
    env = ROOT / ".env"
    for line in env.read_text(encoding="utf-8").splitlines() if env.exists() else []:
        name, _, value = line.partition("=")
        if name.strip() == "OPENROUTER_API_KEY":
            return value.strip().strip("\"'")
    return ""


class Session:
    """The chat on a copy of the data folder, as the app uses it."""

    def __init__(self, source: Path, workdir: Path):
        shutil.copytree(source, workdir, dirs_exist_ok=True) if source.exists() else workdir.mkdir(exist_ok=True)
        os.environ["FANTASY_DATA_DIR"] = str(workdir)
        for var in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"):
            os.environ[var] = "false"
        self.conn = connect()
        migrate(self.conn)
        self.key = get_setting(self.conn, "openrouter_api_key") or env_key()
        if not self.key:
            sys.exit("No OpenRouter key: save one in the app settings or set OPENROUTER_API_KEY in .env")
        set_settings(self.conn, {"openrouter_api_key": self.key})
        if not get_setting(self.conn, "current_league_id"):
            put_settings(SettingsBody(sleeper_username=get_setting(self.conn, "sleeper_username") or "OrMatalon"),
                         self.conn)
            put_league(LeagueBody(league_name="Or Test League"), self.conn)
        self.league_id = get_setting(self.conn, "current_league_id")
        self.model = get_setting(self.conn, "model", DEFAULT_MODEL)
        self.base = base_url(self.conn)
        self._names = None

    def remaining(self) -> int | None:
        """Free requests left today; None when the model is not free or the account has no counter."""
        counter = llm.free_requests(self.base, self.key) if self.model.endswith(":free") else None
        return counter["remaining"] if counter else None

    def fresh(self) -> None:
        chat.start_fresh(self.conn, self.league_id)

    def names(self) -> dict:
        if self._names is None:
            self._names = league_names(self.conn, self.league_id)
        return self._names

    def resolve(self, text: str) -> str | None:
        found = {k: self.names().get(k) for k in re.findall(r"\{(\w+)\}", text)}
        return None if None in found.values() else text.format_map(found)

    def ask(self, text: str) -> dict:
        events = []
        try:
            qid = chat.start_question(self.conn, text)
        except ApiError as e:
            if e.code == "quota_exceeded":
                raise QuotaStop("OpenRouter says the daily quota is used") from None
            return turn_from(text, events, [], {"code": e.code, "message": e.message})
        chat.run_question(self.conn, qid, lambda event, payload: events.append((event, payload)))
        rows = chat.trace(self.conn, self.league_id)["rows"]
        turn = turn_from(text, events, rows, None, qid)
        if (turn["error"] or {}).get("code") == "quota_exceeded":
            raise QuotaStop("OpenRouter says the daily quota is used")
        return turn


def turn_from(text: str, events: list, rows: list[dict], error: dict | None, question_id: int | None = None) -> dict:
    """One question as the runner records it: answer, tool calls with results, the text the answer may rely on."""
    answer = next((p["text"] for e, p in events if e == "answer"), None)
    error = error or next((p for e, p in events if e == "error"), None)
    mine = [r for r in rows if r["meta"].get("question_id") == question_id]
    calls = [{"name": r["meta"]["name"], "arguments": r["meta"]["arguments"], "ok": r["meta"]["ok"],
              "result": r["content"]} for r in mine if r["role"] == "tool"]
    # grounding text: context blocks, every tool result and the manager's own words in this chat (not earlier answers)
    grounding = "\n".join(
        [r["meta"].get("context", "") for r in rows if r["role"] == "assistant"]
        + [r["content"] for r in rows if r["role"] in ("tool", "user")])
    steps = [p["tool"] for e, p in events if e == "step" and p["kind"] == "tool" and p["status"] != "running"]
    return {"question": text, "answer": answer, "error": error, "tools": calls, "grounding": grounding,
            "steps": steps, "model_turns": next((p["model_turns"] for e, p in events if e == "answer"), None)}


def league_names(conn, league_id: str) -> dict:
    """Real names from the stored league for the {placeholders} in the questions."""
    me = conn.execute("select roster_id from fantasy_team where league_id = ? and owner_id = ?",
                      (league_id, get_setting(conn, "sleeper_user_id"))).fetchone()
    me = me[0] if me else -1

    def one(sql, *args):
        row = conn.execute(sql, (league_id, *args)).fetchone()
        return row[0] if row else None

    mine = ("select p.full_name from roster_player r join player p on p.player_id = r.player_id "
            "where r.league_id = ? and r.roster_id = ? and p.position in ('QB','RB','WR','TE') ")
    order = "order by p.search_rank is null, p.search_rank"
    return {
        "my_starter": one(mine + "and r.role = 'starter' " + order, me),
        "my_bench": one(mine + "and r.role = 'bench' " + order, me),
        "my_wr": one(mine + "and p.position = 'WR' " + order, me),
        "my_injured": one(mine + "and coalesce(p.injury_status, '') != '' " + order, me),
        "my_ir": one(mine + "and r.role = 'reserve' " + order, me),
        "other_fantasy_team": one("select name from fantasy_team where league_id = ? and roster_id != ? "
                                  "order by roster_id", me),
        "other_player": one("select p.full_name from roster_player r join player p on p.player_id = r.player_id "
                            "where r.league_id = ? and r.roster_id != ? and p.position in ('QB','RB','WR','TE') "
                            + order, me),
        "free_agent": one("select p.full_name from player p where p.position in ('RB','WR','TE') and p.active = 1 "
                          "and p.nfl_team is not null and p.search_rank is not null "
                          "and p.player_id not in (select player_id from roster_player where league_id = ?) "
                          "order by p.search_rank"),
    }


# ---- the optional T2 judge ----------------------------------------------------------------------------------------

RUBRIC = ("Score each answer to a fantasy football manager from 1 to 5 on: clear (one reading, plain terms), direct "
          "(the answer is in the first sentence), natural (reads like a person), not_padded (nothing can be cut). "
          "Reply with only a JSON list, one object per answer in order: "
          '{"i": 1, "clear": 5, "direct": 5, "natural": 5, "not_padded": 5}.')
CRITERIA = ("clear", "direct", "natural", "not_padded")


def judge_passes(scores: dict) -> bool:
    """T2 per answer: no score below 3 and mean at least 4.0."""
    values = [scores[c] for c in CRITERIA]
    return min(values) >= 3 and sum(values) / len(values) >= 4.0


def judge(session: Session, items: list[tuple[str, str]]) -> list[dict]:
    """One request for up to 12 (question, answer) pairs; returns the scores in order."""
    body = "\n\n".join(f"{i}. QUESTION: {q}\nANSWER: {a}" for i, (q, a) in enumerate(items, 1))
    client = llm.client(session.base, session.key)
    msg, _ = llm.complete(client, JUDGE_MODEL, [{"role": "system", "content": RUBRIC},
                                                {"role": "user", "content": body}], None, session.base, session.key)
    text = msg["content"]
    return json.loads(text[text.index("["): text.rindex("]") + 1])


# ---- report -----------------------------------------------------------------------------------------------------

def cell(text: str, limit: int = 140) -> str:
    text = " ".join(str(text).split()).replace("|", "/")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def redact(text: str, key: str) -> str:
    text = text.replace(key, "[key]") if key else text
    return re.sub(r"sk-or-[A-Za-z0-9_-]+", "[key]", text)


def table(checks: list[Check], state: dict) -> str:
    rows = ["| id | question | tools expected | tools actual | words | numbers not in tool text | result | reason |",
            "|---|---|---|---|---|---|---|---|"]
    for c in checks:
        r = state["results"].get(c.id)
        if c.needs:
            r = {"status": "skipped", "reason": "needs " + c.needs, "turns": [], "actual": [], "grades": [],
                 "expected": expected_text(c)}
        elif not r:
            r = {"status": "not run", "reason": state.get("stopped", ""), "turns": [], "actual": [], "grades": [],
                 "expected": expected_text(c)}
        grades = [g for g in r.get("grades", []) if g]
        rows.append("| " + " | ".join([
            c.id, cell(" / ".join(c.questions), 80), cell(r["expected"], 60), cell(", ".join(r["actual"]) or "none", 60),
            "/".join(f"{g['words']}" for g in grades) or "-",
            cell(", ".join(n for g in grades for n in g["numbers"]) or "none", 40),
            r["status"], cell(r["reason"], 160)]) + " |")
    return "\n".join(rows)


def write_report(checks: list[Check], state: dict, quota: dict, key: str) -> Path:
    RESULTS.mkdir(exist_ok=True)
    day = date.today().isoformat()
    results = [state["results"].get(c.id) for c in checks if not c.needs]
    counts = {s: sum(1 for r in results if r and r["status"] == s) for s in ("pass", "fail", "error", "skipped")}
    md = (f"# Live checks {day}\n\nModel {quota.get('model')}. Free requests left: {quota.get('start')} at start, "
          f"{quota.get('end')} at end. Started {state['started']}.\n\n"
          f"Pass {counts['pass']}, fail {counts['fail']}, error {counts['error']}, skipped at run time "
          f"{counts['skipped']}, not run {sum(1 for c in checks if not c.needs) - sum(counts.values())}, needs setup "
          f"{sum(1 for c in checks if c.needs)}.\n"
          + (f"\nStopped: {state['stopped']}. Continue with `uv run python -m tests.live --resume`.\n"
             if state.get("stopped") else "")
          + "\nA pass means the automatic checks passed (right tool, word cap, grounded numbers, wording). Compare the "
            "facts with STATE and PANEL by hand.\n\n" + table(checks, state) + "\n")
    if state.get("judge"):
        md += "\n## T2 judge\n\n" + state["judge"]["summary"] + "\n"
    out = RESULTS / f"{day}.md"
    out.write_text(redact(md, key), encoding="utf-8")
    (RESULTS / f"{day}.json").write_text(redact(json.dumps(state, indent=1), key), encoding="utf-8")
    return out


def run_judge(session: Session, state: dict) -> None:
    items = [(t["question"], t["answer"]) for r in state["results"].values() if r["status"] == "pass"
             for t in r["turns"] if t["answer"]]
    scores = []
    for i in range(0, len(items), 12):
        scores += judge(session, items[i:i + 12])
    passed = sum(judge_passes(s) for s in scores)
    state["judge"] = {"scores": scores, "summary": f"{passed} of {len(scores)} judged answers pass "
                                                   f"(model {JUDGE_MODEL}); suite needs at least 90%."}


# ---- command line -------------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tests.live", description=__doc__.split("\n\n")[0])
    p.add_argument("select", nargs="*", help="check ids or groups: v1 adversarial later tone (default: all but tone)")
    p.add_argument("--source", type=Path, default=ROOT / "data", help="data folder to copy (default data/)")
    p.add_argument("--resume", action="store_true", help="continue the last run")
    p.add_argument("--plan", action="store_true", help="print the plan and exit; no model call")
    p.add_argument("--judge", action="store_true", help="T2 judge over saved answers (uses requests)")
    p.add_argument("--strict-numbers", action="store_true", help="ungrounded numbers fail in every group (D3)")
    args = p.parse_args(argv)

    state_path = RESULTS / "state.json"
    old = load_state(state_path) if args.resume else None
    if args.resume and not old:
        sys.exit("Nothing to resume: no tests/results/state.json")
    selection = args.select or (old["selection"] if old else [])
    checks = select(selection)
    if not checks and not (args.judge or "tone" in [s.lower() for s in selection]):
        sys.exit("No check matches " + " ".join(selection))
    print("plan:", plan(checks))
    if args.plan:
        return 0

    state = old or new_state(selection)
    RESULTS.mkdir(exist_ok=True)

    def save(s):
        state_path.write_text(redact(json.dumps(s, indent=1), session.key), encoding="utf-8")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        try:
            session = Session(args.source, Path(tmp) / "data")
            start = session.remaining()
            print(f"free requests left: {start}; a question takes about {REQUESTS_PER_QUESTION}; "
                  f"stops below {MIN_LEFT}")
            if checks:
                run_checks(checks, session, state, save, args.strict_numbers)
            if args.judge or "tone" in [s.lower() for s in selection]:
                run_judge(session, state)
            save(state)
            out = write_report(checks, state, {"model": session.model, "start": start, "end": session.remaining()},
                               session.key)
        except ApiError as e:
            sys.exit(f"{e.code}: {e.message}")
        finally:
            if "session" in locals():
                session.conn.close()
    print("report:", out, "| stopped:" if state.get("stopped") else "", state.get("stopped", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
