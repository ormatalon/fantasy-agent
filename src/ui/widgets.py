"""Colors (AGENT.md and design/README.md tokens), CSS and small shared pieces."""

from datetime import datetime

from nicegui import ui

CSS = """
:root { --yellow:#ecad0a; --blue:#209dd7; --purple:#753991; --navy:#032147; --gray:#888888; --gray-strong:#5c6670;
  --blue-dark:#0e6f9e; --text:#1c2733; --bg:#f4f6f9; --line:#dde3ea; --danger:#b42318; --ok:#1a7f4b;
  --yellow-tint:#fdf5dc; --danger-tint:#fdecea; --blue-tint:#e6f4fb; --ok-tint:#e7f5ee; }
body { font-family:"Segoe UI",system-ui,sans-serif; font-size:14px; color:var(--text); background:var(--bg); }
.nicegui-content { padding:0 !important; gap:0 !important; align-items:stretch; height:100vh; }
.window { min-width:960px; height:100vh; display:flex; flex-direction:column; flex-wrap:nowrap; gap:0; }
.topbar { height:52px; flex:none; background:#fff; border-bottom:3px solid var(--yellow); display:flex;
  flex-wrap:nowrap; align-items:center; gap:16px; padding:0 24px; }
.brand { font-weight:700; font-size:16px; color:var(--navy); }
.main { flex:1; min-height:0; display:flex; flex-wrap:nowrap; gap:0; align-items:stretch; }
.chat { flex:1; min-width:0; display:flex; flex-direction:column; flex-wrap:nowrap; gap:0; }
.thread { flex:1; min-height:0; overflow-y:auto; padding:24px; }
.thread-inner { max-width:760px; margin:0 auto; display:flex; flex-direction:column; gap:12px; }
.composer { border-top:1px solid var(--line); background:#fff; padding:12px 24px; }
.composer-inner { max-width:760px; margin:0 auto; display:flex; flex-direction:column; gap:4px; }
.msg-user { align-self:flex-end; background:var(--navy); color:#fff; border-radius:8px; padding:8px 12px; max-width:80%; }
.msg-user.unsent { background:#fff; color:var(--navy); border:1px dashed var(--navy); }
.msg-agent { background:#fff; border:1px solid var(--line); border-left:4px solid var(--yellow); border-radius:8px; padding:12px 16px; }
.when { font-size:12px; color:var(--gray-strong); }
.progress { background:#fff; border:1px solid var(--line); border-left:4px solid var(--blue); border-radius:8px; padding:12px 16px; gap:8px; }
.progress .head { display:flex; justify-content:space-between; font-weight:600; color:var(--navy); }
.banner { border-radius:4px; padding:12px 16px; border-left:4px solid; display:flex; gap:12px; }
.banner .grow { flex:1; gap:4px; display:flex; flex-direction:column; }
.btitle { font-weight:700; color:var(--navy); }
.banner-warn { background:var(--yellow-tint); border-color:var(--yellow); }
.banner-error { background:var(--danger-tint); border-color:var(--danger); }
.banner-info { background:var(--blue-tint); border-color:var(--blue); }
.hint { font-size:12px; color:var(--gray-strong); }
.muted { color:var(--gray-strong); }
h1.t, h2.t, h3.t { color:var(--navy); margin:0; font-weight:600; line-height:1.25; }
h1.t { font-size:26px; } h2.t { font-size:20px; } h3.t { font-size:16px; }
.empty { align-items:center; text-align:center; gap:12px; max-width:520px; margin:auto; padding:48px 0; }
.rule { width:48px; height:3px; background:var(--yellow); }
.panel { width:360px; flex:none; overflow-y:auto; background:#fff; border-left:1px solid var(--line); padding:16px; gap:12px; }
.card { background:#fff; border:1px solid var(--line); border-radius:8px; padding:16px; }
.matchup { border-top:3px solid var(--blue); background:#fff; border-radius:8px; padding:12px 16px; border-left:1px solid var(--line);
  border-right:1px solid var(--line); border-bottom:1px solid var(--line); gap:4px; }
.score { font-size:26px; font-weight:700; color:var(--navy); font-variant-numeric:tabular-nums; }
.score.lead { border-bottom:3px solid var(--yellow); }
.player { display:flex; flex-wrap:nowrap; align-items:center; gap:8px; padding:6px 0; border-bottom:1px solid var(--line); }
.slot { width:44px; flex:none; font-size:12px; font-weight:600; color:var(--gray-strong); }
.pname { font-weight:600; color:var(--text); }
.pts { font-variant-numeric:tabular-nums; font-weight:600; }
.chip { display:inline-block; font-size:12px; font-weight:700; border-radius:10px; padding:0 8px; border:1px solid; white-space:nowrap; }
.chip-q { background:var(--yellow-tint); border-color:var(--yellow); color:#6b4d00; }
.chip-d { background:#fde6cf; border-color:#d9822b; color:#7a3d00; }
.chip-o { background:var(--danger-tint); border-color:var(--danger); color:var(--danger); }
.chip-no { background:#eef0f3; border-color:#b7c0ca; color:var(--gray-strong); }
.sr-only { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0,0,0,0); }
.skel { background:linear-gradient(90deg,#e8ecf1 25%,#f3f5f8 50%,#e8ecf1 75%); background-size:200% 100%;
  animation:shimmer 1.4s infinite; border-radius:4px; height:14px; width:100%; }
@keyframes shimmer { to { background-position:-200% 0; } }
@media (prefers-reduced-motion: reduce) { .skel { animation:none !important; } .q-spinner { animation-duration:3s; } }
:focus-visible { outline:3px solid var(--blue) !important; outline-offset:2px; }
.option { display:flex; flex-wrap:nowrap; gap:12px; align-items:flex-start; padding:12px 16px; border:1px solid var(--line);
  border-radius:8px; cursor:pointer; background:#fff; width:100%; }
.option.selected { border-color:var(--purple); background:#f3ebf7; }
.option.disabled { background:#f6f7f9; cursor:not-allowed; color:var(--gray-strong); }
.option .dot { width:16px; height:16px; border:2px solid #b7c0ca; border-radius:50%; flex:none; margin-top:3px; }
.option.selected .dot { border-color:var(--purple); background:radial-gradient(var(--purple) 45%, transparent 50%); }
.option .title { font-weight:600; color:var(--navy); }
.option.disabled .title { color:var(--gray-strong); }
.chip-rec { background:#f3ebf7; border-color:var(--purple); color:var(--purple); }
.chip-free { background:var(--ok-tint); border-color:var(--ok); color:var(--ok); }
.setup { max-width:640px; margin:0 auto; padding:24px 0 48px; gap:24px; width:100%; }
.section { background:#fff; border:1px solid var(--line); border-radius:8px; padding:24px; gap:12px; }
.stepno { width:24px; height:24px; border-radius:50%; background:var(--navy); color:#fff; font-weight:700; font-size:13px;
  display:inline-grid; place-items:center; flex:none; }
.keysaved { background:var(--ok-tint); border-left:4px solid var(--ok); border-radius:4px; padding:12px 16px; }
.err-text { font-size:13px; color:var(--danger); }
.disabled-league { color:var(--gray-strong); background:#f6f7f9; border:1px solid var(--line); border-radius:8px; padding:8px 16px; }
"""

CHIPS = {  # injury_status -> (letters, css class, meaning)
    "Questionable": ("Q", "chip-q", "Questionable"),
    "Doubtful": ("D", "chip-d", "Doubtful"),
    "Out": ("OUT", "chip-o", "Out"),
    "IR": ("IR", "chip-o", "Injured reserve"),
    "PUP": ("PUP", "chip-o", "Physically unable to perform"),
    "Sus": ("SUS", "chip-o", "Suspended"),
    "DNR": ("DNR", "chip-o", "Did not report"),
    "NA": ("NA", "chip-no", "Not active"),
}


def style() -> None:
    """Per page: CSS, and Quasar's primary color is the purple of the main action."""
    ui.add_css(CSS)
    ui.colors(primary="#753991", secondary="#032147", accent="#209dd7")


def chip(letters: str, css: str, meaning: str = "") -> None:
    """A chip with letters, never color alone. The meaning shows as a tooltip."""
    with ui.label(letters).classes(f"chip {css}"):
        if meaning:
            ui.tooltip(meaning)


def skeleton(height: int = 14, width: str = "100%") -> None:
    ui.element("div").classes("skel").style(f"height:{height}px;width:{width}")


def option_card(selected: bool, on_click=None, disabled: bool = False):
    """A bordered radio option (design/setup.html). Fill it with `with`."""
    card = ui.element("div").classes("option" + (" selected" if selected else "") + (" disabled" if disabled else ""))
    card.props(f'role="radio" aria-checked="{str(selected).lower()}"' + (' aria-disabled="true"' if disabled else ' tabindex="0"'))
    if on_click and not disabled:
        for event in ("click", "keydown.enter", "keydown.space.prevent"):
            card.on(event, on_click)
    with card:
        ui.element("div").classes("dot")
    return card


def banner(kind: str, title: str, text: str = ""):
    """kind: warn (yellow), error (red), info (blue). Returns the inner column so a caller can add buttons."""
    with ui.element("div").classes(f"banner banner-{kind}").props('role="alert"'):
        with ui.element("div").classes("grow") as inner:
            ui.label(title).classes("btitle")
            if text:
                ui.label(text)
    return inner


def when(iso: str | None) -> str:
    """UTC ISO time as local 'Mon 4:02 PM'."""
    if not iso:
        return ""
    t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    return t.strftime("%a %I:%M %p").replace(" 0", " ")


SOURCE_NAMES = {
    "espn": "NFL data from ESPN",
    "nflverse": "NFL data from nflverse",
    "sleeper_stats": "Sleeper stats",
    "sleeper_projections": "Sleeper projections",
    "sleeper_catalog": "Sleeper player list",
}


def sleeper_failed(failed: list[dict]) -> bool:
    """Only the Sleeper roster and matchup source means "moves made since then are not shown"."""
    return any(f["source"] == "sleeper" for f in failed)


def nfl_source_lines(failed: list[dict]) -> list[str]:
    """One neutral line per failed NFL source: what failed and its last good time."""
    return [
        f"{SOURCE_NAMES.get(f['source'], f['source'])} "
        + (f"last updated {when(f['since'])}." if f["since"] else "has not been updated yet.")
        for f in failed if f["source"] != "sleeper"
    ]


DEFENSE = {"idp": "IDP", "team_def": "Team DEF", "none": "No defense"}
PPR = {1.0: "PPR", 0.5: "Half PPR", 0.0: "Standard"}


def scoring(defense: str | None, reception_points: float | None) -> str:
    return " · ".join(x for x in (DEFENSE.get(defense, ""), PPR.get(reception_points, "")) if x)
