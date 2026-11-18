"""My fantasy team panel: roster with injury chips, matchup, refresh."""

from nicegui import run, ui
from web.errors import ApiError

from . import backend
from .widgets import CHIPS, banner, chip, nfl_source_lines, skeleton, sleeper_failed, when

OUT_LIKE = {"Out", "IR", "PUP", "Sus", "DNR", "NA"}  # a starter with one of these gets the warning
MATCHUP_STATE = {"not_started": "Not started", "live": "Live", "final": "Final"}


class Panel:
    def __init__(self, open_settings):
        self.open_settings = open_settings
        self.data: dict | None = None
        self.error: ApiError | None = None
        self.loading = True
        self.refreshing = False

    async def load(self, force: bool = False) -> None:
        self.refreshing = force
        self.view.refresh()
        try:
            self.data, self.error = await run.io_bound(backend.panel_data, force), None
        except ApiError as e:
            self.error = e
        self.loading = self.refreshing = False
        self.view.refresh()

    @ui.refreshable
    def view(self) -> None:
        if self.loading:
            with ui.column().classes("w-full gap-2").props('role="status" aria-busy="true"'):
                ui.label("Loading your fantasy team").classes("sr-only")
                skeleton(96)
                skeleton(14, "40%")
                for _ in range(8):
                    skeleton()
        elif self.error and not self.data:
            self._error()
        elif self.data:
            self._panel(self.data)

    def _error(self) -> None:
        e = self.error
        if e.code == "no_league":
            ui.label("No league chosen").classes("t btitle")
            ui.label("Choose a league in settings to see your fantasy team.").classes("muted")
            ui.button("Open settings", on_click=self.open_settings)
            return
        inner = banner("error", "Could not load your fantasy team.", e.message)
        with inner:
            ui.button("Try again", on_click=self.load).props("outline color=secondary dense no-caps")

    def _panel(self, p: dict) -> None:
        team, refresh = p["fantasy_team"], p["refresh"]
        with ui.row().classes("w-full items-center justify-between no-wrap"):
            ui.label(team["name"]).classes("text-xl font-semibold").style("color:#032147")
            ui.button("Refreshing" if self.refreshing else "Refresh", on_click=lambda: self.load(True)) \
                .props("flat dense no-caps color=accent").set_enabled(not self.refreshing)
        ui.label(f"Record {team['record']} · Rank {team['rank']} · Week {p['week']}").classes("muted")
        ui.label(f"Updated {when(refresh['stored_at'])}").classes("hint")
        if sleeper_failed(refresh["failed"]):
            banner("warn", "Could not refresh from Sleeper.",
                   f"Showing data stored on {when(refresh['stored_at'])}. A move you made after that is not included.")
        for line in nfl_source_lines(refresh["failed"]):
            banner("warn", line)
        self._matchup(p["matchup"])
        warnings = self._warnings(p["slots"])
        if warnings:
            banner("warn", "Check your starters.", warnings)
        for title, role in (("Starters", "starter"), ("Bench", "bench"), ("IR", "ir")):
            rows = [s for s in p["slots"] if s["role"] == role and s["slot"] != "TAXI"]
            if rows:
                ui.label(title).classes("btitle")
                for s in rows:
                    self._player(s)

    def _matchup(self, m: dict | None) -> None:
        with ui.column().classes("matchup w-full"):
            if m is None:
                ui.label("No matchup this week").classes("btitle")
                return
            ui.label(f"vs {m['opponent_fantasy_team']}").classes("btitle")
            if m["state"]:
                ui.label(MATCHUP_STATE[m["state"]]).classes("hint")
            mine, theirs = m["my_score"], m["opponent_score"]
            with ui.row().classes("items-center gap-4"):
                ui.label(f"{mine:.1f}").classes("score" + (" lead" if m["state"] in ("live", "final") and mine > theirs else ""))
                ui.label("to").classes("hint")
                ui.label(f"{theirs:.1f}").classes("score" + (" lead" if m["state"] in ("live", "final") and theirs > mine else ""))

    @staticmethod
    def _warnings(slots: list[dict]) -> str:
        bad = [f"{s['player']['name']} ({CHIPS[s['player']['injury_status']][0]})"
               for s in slots if s["role"] == "starter" and s["player"] and s["player"]["injury_status"] in OUT_LIKE]
        empty = [s["slot"] for s in slots if s["role"] == "starter" and s["player"] is None]
        parts = []
        if bad:
            parts.append("Starters who may not play: " + ", ".join(bad) + ".")
        if empty:
            parts.append("Empty starting slot in Sleeper: " + ", ".join(empty) + ".")
        return " ".join(parts)

    @staticmethod
    def _player(s: dict) -> None:
        p = s["player"]
        with ui.element("div").classes("player w-full"):
            ui.label(s["slot"]).classes("slot")
            if p is None:
                with ui.column().classes("gap-0 grow"):
                    ui.label("Empty slot").classes("pname")
                    ui.label("No player set in Sleeper").classes("hint")
                return
            game = p["game"]
            with ui.column().classes("gap-0 grow"):
                with ui.row().classes("items-center gap-2"):
                    ui.label(p["name"]).classes("pname")
                    if p["injury_status"]:
                        letters, css, meaning = CHIPS.get(p["injury_status"], (p["injury_status"], "chip-no", p["injury_status"]))
                        chip(letters, css, meaning)
                    if game and game["status"] == "bye":
                        chip("BYE", "chip-no", "Bye week")
                meta = f"{p['position']} · {p['nfl_team'] or 'No NFL team'}"
                if game and game["status"] != "bye" and game["opponent_nfl_team"]:
                    meta += f" · {'vs' if game['home'] else 'at'} {game['opponent_nfl_team']}"
                    if game["status"] == "not_started":
                        meta += f", {when(game['kickoff'])}"
                ui.label(meta).classes("hint")
            if s["points"] is not None:
                ui.label(f"{s['points']:.1f}").classes("pts")
