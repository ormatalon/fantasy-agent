# PyInstaller spec for the desktop app. Build the page first, then:
#   cd web && npm ci && npm run build && cd ..
#   uv run --group desktop pyinstaller desktop/fantasy-agent.spec --noconfirm
# Output: dist/FantasyAgent/ (Windows) or dist/FantasyAgent.app (Mac).
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent

a = Analysis(
    [str(ROOT / "src" / "desktop.py")],
    pathex=[str(ROOT)],
    # The prebuilt page, and PuLP's bundled CBC solver (a data file to PyInstaller).
    datas=[(str(ROOT / "web" / "out"), "web/out"), *collect_data_files("pulp")],
    # pandas imports its parquet engine by name at run time, so PyInstaller
    # can't see it; without it the nflverse source is silently skipped.
    hiddenimports=collect_submodules("fastparquet"),
    excludes=["pytest", "matplotlib", "IPython", "tkinter", "notebook"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="FantasyAgent",
    console=False,
    icon=str(ROOT / "desktop" / "icon.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="FantasyAgent")

if sys.platform == "darwin":
    # PyInstaller turns the PNG into the .icns a Mac app needs (uses Pillow).
    app = BUNDLE(
        coll, name="FantasyAgent.app", icon=str(ROOT / "desktop" / "icon.png"),
        bundle_identifier="com.fantasyagent.app",
    )
