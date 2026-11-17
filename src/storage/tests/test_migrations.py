import shutil

import pytest

from storage import db


def tables(conn):
    return {r[0] for r in conn.execute("select name from sqlite_master where type = 'table'")}


def test_creates_database_and_applies_schema(tmp_path):
    conn = db.connect(tmp_path / "new" / "fantasy.db")
    db.migrate(conn)
    assert conn.execute("pragma user_version").fetchone()[0] == 1
    assert {"settings", "chat_message", "roster_diff", "league", "projection", "refresh_log"} <= tables(conn)


def test_upgrade_keeps_user_data(tmp_path):
    old = tmp_path / "migrations_old"
    old.mkdir()
    shutil.copy(db.MIGRATIONS / "0001_init.sql", old)
    conn = db.connect(tmp_path / "fantasy.db")
    db.migrate(conn, old)
    conn.execute("insert into settings values ('sleeper_username', 'OrMatalon')")
    conn.execute("insert into chat_message(league_id, role, content, created_at) values ('1', 'user', 'hi', 't')")
    conn.execute("insert into roster_diff(league_id, roster_id, detected_at, added, dropped) values ('1', 1, 't', '[]', '[]')")

    (old / "0002_later.sql").write_text("alter table settings add column note text; create table extra(x);")
    db.migrate(conn, old)

    assert conn.execute("pragma user_version").fetchone()[0] == 2
    assert conn.execute("select value from settings where key = 'sleeper_username'").fetchone()[0] == "OrMatalon"
    assert conn.execute("select content from chat_message").fetchone()[0] == "hi"
    assert conn.execute("select count(*) from roster_diff").fetchone()[0] == 1


def test_failed_migration_rolls_back_and_names_the_file(tmp_path):
    folder = tmp_path / "m"
    folder.mkdir()
    (folder / "0001_a.sql").write_text("create table a(x);")
    (folder / "0002_bad.sql").write_text("create table b(x); insert into missing values (1);")
    conn = db.connect(tmp_path / "fantasy.db")
    with pytest.raises(RuntimeError, match="0002_bad.sql"):
        db.migrate(conn, folder)
    assert conn.execute("pragma user_version").fetchone()[0] == 1
    assert "b" not in tables(conn)
