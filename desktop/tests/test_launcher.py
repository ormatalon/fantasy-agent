import launcher


def test_picks_a_free_port_when_none_is_set(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("FANTASY_UI_PORT", raising=False)
    monkeypatch.setattr(launcher.os, "environ", launcher.os.environ.copy())
    launcher.configure()
    port = int(launcher.os.environ["FANTASY_UI_PORT"])
    assert 1024 <= port != 8080
    assert launcher.os.environ["FANTASY_DATA_DIR"] == str(tmp_path / "FantasyAgent")


def test_keeps_an_explicit_port(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("FANTASY_UI_PORT", "8123")
    monkeypatch.setattr(launcher.os, "environ", launcher.os.environ.copy())
    launcher.configure()
    assert launcher.os.environ["FANTASY_UI_PORT"] == "8123"
