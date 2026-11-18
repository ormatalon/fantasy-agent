"""Setup and Settings: the same screen. Four steps: username, league, OpenRouter key, model."""

from nicegui import run, ui
from web.errors import ApiError

from . import backend
from .widgets import banner, chip, option_card, scoring

REASONS = {
    "dynasty": "Dynasty league. Season-long redraft only.",
    "keeper": "Keeper league. Season-long redraft only.",
    "best_ball": "Best ball league. Season-long redraft only.",
    "pre_draft": "Not playable yet. The draft has not happened.",
    "drafting": "Not playable yet. The draft is in progress.",
    "complete": "This season is over.",
}


def league_label(l: dict) -> str:
    return f"{l['num_fantasy_teams']} fantasy teams · {scoring(l['defense'], l['reception_points'])}"


class Setup:
    def __init__(self, state: dict, done, cancel=None):
        """done: awaited after a successful save. cancel: given in Settings (already set up), else None."""
        self.done, self.cancel = done, cancel
        self.username = state["username"] or ""
        self.leagues: list[dict] = state["leagues"]
        cur = state["current_league"]
        selectable = [l["league_id"] for l in self.leagues if l["selectable"]]
        self.league_id = cur["league_id"] if cur else (selectable[0] if len(selectable) == 1 else None)
        self.key_set = state["key_set"]
        self.replacing = not self.key_set
        self.key = ""
        self.model = state["model"]
        self.finding = self.saving = False
        self.errors: dict[str, str] = {}

    async def find(self) -> None:
        self.errors.pop("username", None)
        if not self.username.strip():
            self.errors["username"] = "Enter your Sleeper username."
            return self.body.refresh()
        self.finding = True
        self.body.refresh()
        try:
            found = await run.io_bound(backend.find_leagues, self.username.strip())
        except ApiError as e:
            self.errors["username"] = e.message
        else:
            self.username, self.leagues = found["username"], found["leagues"]
            ids = [l["league_id"] for l in self.leagues if l["selectable"]]
            if self.league_id not in ids:
                self.league_id = ids[0] if len(ids) == 1 else None
        self.finding = False
        self.body.refresh()

    async def save(self) -> None:
        self.errors.clear()
        if not self.username.strip():
            self.errors["username"] = "Enter your Sleeper username."
        if not self.league_id:
            self.errors["league"] = "Choose a league."
        if self.replacing and not self.key.strip():
            self.errors["key"] = "Enter your OpenRouter key."
        if self.errors:
            return self.body.refresh()
        self.saving = True
        self.body.refresh()
        try:
            await run.io_bound(backend.save, self.username.strip(), self.key.strip() or None, self.model, self.league_id)
        except ApiError as e:
            where = {"key_rejected": "key", "provider_error": "key", "username_not_found": "username",
                     "sleeper_unreachable": "username"}.get(e.code, "banner")
            self.errors[where] = e.message + (" Nothing was saved." if e.code == "key_rejected" else "")
            self.saving = False
            return self.body.refresh()
        self.key = ""  # the key is never shown back
        await self.done()

    async def remove_key(self) -> None:
        await run.io_bound(backend.remove_key)
        self.key_set, self.replacing = False, True
        self.body.refresh()

    def build(self) -> None:
        with ui.column().classes("setup"):
            ui.label("Settings" if self.cancel else "Set up Fantasy Agent").classes("text-3xl font-semibold")                 .style("color:#032147")
            ui.label("Changes take effect right away; no restart." if self.cancel
                     else "Four steps. Changes take effect right away; no restart.").classes("muted")
            self.body()

    def _head(self, n: int, title: str) -> None:
        with ui.row().classes("items-center gap-3"):
            ui.label(str(n)).classes("stepno")
            ui.label(title).classes("text-lg font-semibold").style("color:#032147")

    def _err(self, name: str) -> None:
        if name in self.errors:
            ui.label(self.errors[name]).classes("err-text").props('role="alert"')

    @ui.refreshable
    def body(self) -> None:
        if backend.running():
            banner("info", "A question is already running.", "Settings you save apply to the next question.")
        if "banner" in self.errors:
            banner("error", "Could not save.", self.errors["banner"])

        with ui.column().classes("section w-full"):
            self._head(1, "Sleeper username")
            ui.label("Your Sleeper login name, not a league ID. We find your leagues for the current season.").classes("hint")
            with ui.row().classes("w-full items-start no-wrap"):
                ui.input("Username").bind_value(self, "username").props("outlined dense").classes("grow") \
                    .on("keydown.enter", self.find)
                ui.button("Finding leagues" if self.finding else "Find leagues", on_click=self.find) \
                    .props("outline color=secondary no-caps").set_enabled(not self.finding)
            self._err("username")

        with ui.column().classes("section w-full"):
            self._head(2, "League")
            self._leagues()

        with ui.column().classes("section w-full"):
            self._head(3, "OpenRouter key")
            ui.label("The key lets the app ask an AI model your questions. It is stored on this computer only "
                     "and is never shown again.").classes("muted")
            if self.key_set and not self.replacing:
                with ui.row().classes("keysaved w-full items-center justify-between no-wrap"):
                    ui.label("Key saved on this computer").classes("font-semibold")
                    with ui.row():
                        ui.button("Replace key", on_click=self._replace).props("flat dense no-caps color=accent")
                        ui.button("Remove key", on_click=self.remove_key).props("flat dense no-caps color=accent")
            else:
                ui.input("Key", password=True).bind_value(self, "key").props("outlined dense autocomplete=off").classes("w-full")
                self._err("key")
                ui.link("Create a free OpenRouter account and key", "https://openrouter.ai/keys", new_tab=True)

        with ui.column().classes("section w-full"):
            self._head(4, "Model")
            ui.label("Free models work but allow about 50 requests a day, roughly 25 questions. A paid model has no such limit and costs "
                     "credits on your OpenRouter account.").classes("muted")
            with ui.column().classes("w-full gap-2").props('role="radiogroup" aria-label="Model"'):
                for m in backend.models():
                    with option_card(self.model == m["id"], lambda _, m=m: self._pick("model", m["id"])).mark(f"model-{m['id']}"):
                        with ui.column().classes("grow gap-0"):
                            with ui.row().classes("items-center gap-2"):
                                ui.label(m["name"]).classes("title")
                                if m["recommended"]:
                                    chip("Recommended", "chip-rec")
                            ui.label(m["note"]).classes("hint")
                            ui.label(m["id"]).classes("hint")
                        chip("Free", "chip-free") if m["free"] else chip("Paid", "chip-no")

        with ui.row().classes("w-full justify-end items-center"):
            if self.cancel:
                ui.button("Cancel", on_click=self.cancel).props("outline color=secondary no-caps")
            ui.button("Saving" if self.saving else ("Save" if self.cancel else "Save and start"), on_click=self.save) \
                .props("unelevated no-caps").set_enabled(not self.saving)

    def _pick(self, field: str, value: str) -> None:
        setattr(self, field, value)
        self.body.refresh()

    def _replace(self) -> None:
        self.replacing = True
        self.body.refresh()

    def _leagues(self) -> None:
        if not self.leagues:
            ui.label("Your leagues appear here after step 1.").classes("hint")
            return
        if sum(l["selectable"] for l in self.leagues) > 1:
            ui.label("You are in several leagues. Choose the one to work with. You can switch any time in the chat.") \
                .classes("hint")
        with ui.column().classes("w-full gap-2").props('role="radiogroup" aria-label="League"'):
            for l in self.leagues:
                ok = l["selectable"]
                card = option_card(ok and self.league_id == l["league_id"],
                                   lambda _, id=l["league_id"]: self._pick("league_id", id), disabled=not ok)
                with card.mark(f"league-{l['league_id']}"):
                    with ui.column().classes("grow gap-0"):
                        ui.label(l["name"]).classes("title")
                        ui.label(league_label(l)).classes("hint")
                        if not ok:
                            ui.label(REASONS.get(l["not_selectable_reason"], "Not selectable.")).classes("hint")
                    if not ok:
                        chip("Not selectable", "chip-no")
        self._err("league")
