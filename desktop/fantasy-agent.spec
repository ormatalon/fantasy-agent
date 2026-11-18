# PyInstaller one-folder bundle: uv run pyinstaller desktop/fantasy-agent.spec --noconfirm
from PyInstaller.utils.hooks import collect_all, collect_submodules

NOTEBOOK_ONLY = ["matplotlib", "pandas", "seaborn", "IPython", "ipykernel", "jedi", "nbformat", "nbconvert", "docutils", "PIL"]
datas, binaries, hiddenimports = [], [], []
for pkg in ("nicegui", "webview", "tzdata"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("uvicorn")
datas += [("../src/storage/migrations", "storage/migrations")]

a = Analysis(
    ["launcher.py"],
    pathex=["../src"],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=NOTEBOOK_ONLY,  # dev environments have the notebooks group installed; the app does not use it
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="FantasyAgent", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="FantasyAgent")
