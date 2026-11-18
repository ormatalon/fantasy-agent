"""The page: top bar, chat and my fantasy team panel, or the setup screen until setup is complete."""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

from nicegui import app, run, ui
from starlette.middleware.trustedhost import TrustedHostMiddleware
from web import chat as chat_api, main as web_main, panel as panel_api, setup as setup_api
from web.errors import ApiError, api_error_handler

from . import backend
from .panel_view import Panel
from .setup_view import Setup
from .widgets import banner, nfl_source_lines, scoring, skeleton, sleeper_failed, style, when

EXAMPLES = [
    "Is my lineup set for this week?",
    "Who are the best waiver pickups at RB?",
    "Which injured players should I stash?",
    "What does the Seattle depth chart look like at WR?",
]


def error_banner(err: dict, ask_again, open_settings) -> None:
    """The chat error states (design/README.md, Copy for states), keyed on the error code."""
    code = err["code"]
    kind, title, text = "error", "Something went wrong, so there is no answer.", "Nothing was changed in Sleeper."
    buttons = [("Ask again", ask_again)]
    if code == "key_missing":
        kind, title, text = "warn", "Add your OpenRouter key to ask questions.", "Your league and fantasy team still load."
        buttons = [("Open settings", open_settings)]
    elif code == "key_invalid":
        title, text = "OpenRouter refused the saved key.", "The key may have been deleted or mistyped. Replace it in settings, then ask again."
        buttons = [("Open settings", open_settings), ("Ask again", ask_again)]
    elif code == "no_league":
        kind, title, text = "warn", "Choose a league to ask questions.", "No league is chosen yet."
        buttons = [("Open settings", open_settings)]
    elif code == "quota_exceeded":
        kind, title = "warn", "Daily limit reached for this model."
        text = "Wait, choose another model, or add credits to your OpenRouter account."
        buttons = [("Choose another model", open_settings), ("Ask again", ask_again)]
    elif code == "too_many_turns":
        kind, title, text = "warn", "This question needed too many steps.", "Try asking about one position or one player at a time."
    elif code == "league_unsupported":
        kind, title, text = "warn", "This league is not supported.", err["message"]
        buttons = [("Open settings", open_settings)]
    elif code == "provider_error":
        title = err["message"]
    elif code == "sleeper_unreachable":
        text = err["message"]
    inner = banner(kind, title, text)
    with inner:
        with ui.row().classes("gap-3"):
            for i, (label, fn) in enumerate(buttons):
                ui.button(label, on_click=fn).props("unelevated no-caps dense" if i == 0 else "outline color=secondary no-caps dense")


def sources_line(m: dict) -> str:
    parts = [f"{s['label']}, {when(s['as_of'])}" for s in m.get("sources") or []]
    return ("Sources: " + "; ".join(parts) + " · " if parts else "") + when(m["created_at"])


class Chat:
    def __init__(self, state: dict, panel: Panel, open_settings):
        self.panel, self.open_settings = panel, open_settings
        self.messages: list[dict] = []
        self.running: dict | None = None
        self.loading = True
        self.busy = False
        self.notice = ""
        self.steps: dict[int, tuple[str, str]] = {}
        self.t0 = time.monotonic()
        self.watch = None

    def build(self) -> None:
        with ui.element("section").classes("chat"):
            self.thread = ui.element("div").classes("thread").props('aria-live="polite" id=thread')
            with ui.element("div").classes("composer"):
                with ui.element("div").classes("composer-inner"):
                    with ui.row().classes("w-full items-end no-wrap"):
                        self.box = ui.textarea(placeholder="Ask about your league").props("outlined dense autogrow") \
                            .classes("grow")
                        self.box.on("keydown.enter.exact.prevent", self.send)
                        self.send_btn = ui.button("Send", on_click=self.send).props("unelevated no-caps")
                    ui.label("Enter to send, Shift+Enter for a new line. Read-only: the app never changes your Sleeper "
                             "lineup.").classes("hint")

    # --- history ---

    async def reload(self) -> None:
        try:
            h = backend.history()
        except ApiError:  # no league chosen
            h = {"messages": [], "running": None}
        self.messages, self.running = h["messages"], h["running"]
        self.loading = False
        self.render()

    @property
    def failed(self) -> dict | None:
        last = self.messages[-1] if self.messages else None
        return last if last and last["role"] == "user" and last["status"] == "failed" else None

    def render(self) -> None:
        self.thread.clear()
        with self.thread:
            with ui.element("div").classes("thread-inner"):
                if self.notice:
                    banner("info", self.notice)
                if self.loading:
                    with ui.column().classes("w-full gap-3").props('aria-busy="true"'):
                        skeleton(44, "40%")
                        skeleton(96, "75%")
                        skeleton(44, "30%")
                elif not self.messages:
                    self._empty()
                for m in self.messages:
                    self._message(m)
                self.progress_box = ui.column().classes("w-full")
        failed = self.failed
        if failed and not self.busy:
            self.box.value = failed["text"]
        # a refused key locks the input (the text stays) until a newer key is saved in Settings
        locked = bool(failed and failed.get("error", {}).get("code") == "key_invalid"
                      and failed["created_at"] >= backend.key_saved_at())
        self.box.set_enabled(not self.busy and not locked)
        self.send_btn.set_enabled(not self.busy and not locked)
        self.send_btn.set_text("Working" if self.busy else "Send")
        ui.run_javascript("const t=document.getElementById('thread'); t && (t.scrollTop = t.scrollHeight)")

    def _empty(self) -> None:
        with ui.column().classes("empty"):
            ui.element("div").classes("rule")
            ui.label("Ask about your league").classes("text-xl font-semibold").style("color:#032147")
            ui.label("Lineups, waiver pickups, injured stashes, trades, and how any NFL team or player is doing. "
                     "Answers come from the league and NFL data the app has fetched, and each says where it came from.") \
                .classes("muted")
            with ui.row().classes("justify-center"):
                for q in EXAMPLES:
                    ui.button(q, on_click=lambda _, q=q: self.box.set_value(q)).props("outline color=accent no-caps dense")

    def _message(self, m: dict) -> None:
        if m["role"] == "user":
            failed = m["status"] == "failed"
            with ui.column().classes("msg-user" + (" unsent" if failed else "")).props('data-role="user"'):
                ui.label(m["text"])
                ui.label("Not answered. Your question is kept below." if failed else when(m["created_at"])) \
                    .classes("when").style("color:inherit;opacity:.8")
            if failed and m is self.messages[-1]:
                error_banner(m["error"], self.send, self.open_settings)
            return
        refresh = m.get("refresh") or {}
        for line in nfl_source_lines(refresh.get("failed") or []):
            banner("warn", line)
        if sleeper_failed(refresh.get("failed") or []):
            banner("warn", "Could not refresh from Sleeper.",
                   f"This answer uses data stored on {when(refresh['stored_at'])}. A move you made after that is not included.")
        with ui.column().classes("msg-agent w-full").props('data-role="assistant"'):
            ui.markdown(m["text"])
            ui.label(sources_line(m)).classes("when")

    # --- asking ---

    async def send(self) -> None:
        text = (self.box.value or "").strip()
        if not text or self.busy:
            return
        failed = self.failed
        self.box.value, self.notice = "", ""
        self.busy = True
        try:
            question_id = await run.io_bound(backend.start_question, text, failed["id"] if failed else None)
        except ApiError as e:
            self.busy = False
            if e.code == "question_in_progress":
                self.box.value, self.notice = text, e.message
                return self.render()
            return await self.reload()  # the question is stored as failed, with its error
        await self.reload()
        self.show_progress(0)
        loop, events = asyncio.get_running_loop(), asyncio.Queue()
        worker = asyncio.ensure_future(run.io_bound(
            backend.run_question, question_id, lambda e, p: loop.call_soon_threadsafe(events.put_nowait, (e, p))))
        while (item := await events.get())[0] != "end":
            if item[0] == "step":
                p = item[1]
                self.steps[p["id"]] = (p["label"], p["status"])
                self.render_steps()
        await worker
        await self.finished()

    async def finished(self) -> None:
        self.busy = False
        await self.reload()
        await self.panel.load()  # the question refreshed Sleeper data

    def show_progress(self, elapsed_ms: int) -> None:
        self.steps, self.t0 = {}, time.monotonic() - elapsed_ms / 1000
        with self.progress_box:
            with ui.column().classes("progress w-full").props('role="status"'):
                with ui.element("div").classes("head w-full"):
                    ui.label("Working on your question")
                    self.elapsed = ui.label("0:00")
                self.steps_box = ui.column().classes("gap-1")
                ui.label("A question can take a minute. You can keep reading your fantasy team on the right.").classes("hint")
                ui.timer(1.0, self.tick)

    def tick(self) -> None:
        s = int(time.monotonic() - self.t0)
        self.elapsed.set_text(f"{s // 60}:{s % 60:02d}")

    def render_steps(self) -> None:
        self.steps_box.clear()
        with self.steps_box:
            for label, status in self.steps.values():
                with ui.row().classes("items-center gap-2"):
                    if status == "running":
                        ui.spinner(size="xs")
                    else:
                        ui.label("✓" if status == "done" else "!").style("width:16px")
                    ui.label(label)

    async def resume(self, running: dict) -> None:
        """A question started before this page loaded (reload, or another window) is still running."""
        self.busy = True
        self.render()
        started = datetime.fromisoformat(running["started_at"].replace("Z", "+00:00"))
        self.show_progress(int((datetime.now(timezone.utc) - started).total_seconds() * 1000))
        self.watch = ui.timer(2.0, self.poll)

    async def poll(self) -> None:
        if backend.running() is None:
            self.watch.deactivate()
            await self.finished()


def topbar(state: dict, open_settings, start_fresh, switch) -> None:
    league = state["current_league"]
    with ui.element("header").classes("topbar"):
        ui.label("Fantasy Agent").classes("brand")
        if league:
            ok = {l["league_id"]: l["name"] for l in state["leagues"] if l["selectable"]}
            if len(ok) > 1:
                ui.select(ok, value=league["league_id"], on_change=lambda e: switch(e.value)).props("dense outlined")
            else:
                ui.label(league["name"]).classes("font-semibold")
            fmt = league["format"]
            ui.label(" · ".join(x for x in (f"Season {league['season']}", f"Week {league['this_week']}",
                                            scoring(fmt.get("defense"), fmt.get("reception_points"))) if x)).classes("muted")
        ui.element("div").classes("grow")
        if league:
            ui.button("Start fresh", on_click=start_fresh).props("outline color=secondary no-caps dense")
        ui.button("Settings", on_click=open_settings).props("outline color=secondary no-caps dense")


async def index() -> None:
    """The one page. `view` is 'chat' or 'setup'; setup shows until username, league and key are all set."""
    style()
    page = ui.element("div").classes("window")

    async def show(view: str | None = None) -> None:
        state = backend.state()
        page.clear()
        with page:
            if (view or ("chat" if state["setup_complete"] else "setup")) == "setup":
                with ui.element("div").classes("thread"):
                    Setup(state, done=show, cancel=(lambda: show("chat")) if state["setup_complete"] else None).build()
                return
            await chat_page(state)

    async def chat_page(state: dict) -> None:
        panel = Panel(open_settings=lambda: show("setup"))
        chat = Chat(state, panel, open_settings=lambda: show("setup"))

        async def start_fresh() -> None:
            with ui.dialog() as dlg, ui.card():
                ui.label(f"Start fresh in {state['current_league']['name']}?").classes("text-lg font-semibold")
                ui.label("This deletes the chat history for this league. Your league and settings stay.")
                with ui.row().classes("justify-end w-full"):
                    ui.button("Cancel", on_click=lambda: dlg.submit(False)).props("outline color=secondary no-caps")
                    ui.button("Start fresh", on_click=lambda: dlg.submit(True)).props("unelevated no-caps")                         .mark("confirm-fresh")
            if await dlg:
                backend.start_fresh()
                await chat.reload()

        async def switch(league_id: str) -> None:
            try:
                await run.io_bound(backend.switch_league, league_id)
            except ApiError as e:
                ui.notify(e.message)
            await show("chat")

        topbar(state, lambda: show("setup"), start_fresh, switch)
        with ui.element("div").classes("main"):
            chat.build()
            with ui.element("aside").classes("panel").props('aria-label="My fantasy team"'):
                panel.view()
        await chat.reload()
        if chat.running:
            await chat.resume(chat.running)
        ui.timer(0, panel.load, once=True)
        ui.timer(60, panel.load)

    await show()


def allow_local_hosts_only(target) -> None:
    """Same host check as web.main: stops DNS rebinding, which binding to 127.0.0.1 does not."""
    target.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])


def serve(host: str | None = None, port: int | None = None, native: bool | None = None,
          log_file: str | None = None) -> None:
    """One process: this page at / and the same /api the Next.js page used. Arguments default to the environment:
    FANTASY_UI_HOST (127.0.0.1), FANTASY_UI_PORT (8080), FANTASY_UI_NATIVE=1 (desktop window, off),
    FANTASY_LOG_FILE (no file). Only localhost and 127.0.0.1 are accepted as Host, whatever the bind address."""
    host = host or os.environ.get("FANTASY_UI_HOST", "127.0.0.1")
    port = port or int(os.environ.get("FANTASY_UI_PORT", "8080"))
    native = os.environ.get("FANTASY_UI_NATIVE") == "1" if native is None else native
    log_file = log_file or os.environ.get("FANTASY_LOG_FILE")
    allow_local_hosts_only(app)
    app.add_exception_handler(ApiError, api_error_handler)
    for router in (setup_api.router, panel_api.router, chat_api.router):
        app.include_router(router)

    @app.get("/api/health")
    def health():
        return web_main.health()

    @app.on_startup
    async def start() -> None:
        if log_file:  # here, because uvicorn resets logging when it starts
            handler = logging.FileHandler(log_file, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
            for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access", "nicegui"):
                logging.getLogger(name).addHandler(handler)
            logging.getLogger().setLevel(logging.INFO)
            for noisy in ("httpx", "httpcore", "openai"):  # the key only travels in a header; keep client chatter out anyway
                logging.getLogger(noisy).setLevel(logging.WARNING)
            logging.getLogger(__name__).info("serving on %s:%s", host, port)
        from storage import db
        conn = db.connect()
        db.migrate(conn)
        conn.close()
        if os.environ.get("FANTASY_POLL", "1") == "1":
            app.state.poll = asyncio.create_task(web_main.poll_loop())

    ui.page("/")(index)
    if native:
        app.native.window_args["min_size"] = (960, 640)
    ui.run(host=host, port=port, title="Fantasy Agent", show=False, reload=False,
           native=native, window_size=(1280, 800) if native else None)
