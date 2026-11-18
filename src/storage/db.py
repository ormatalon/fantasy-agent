"""SQLite file, connection and in-place migrations (docs/data-contract.md section 1)."""

import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS = Path(__file__).parent / "migrations"


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def data_dir() -> Path:
    if env := os.environ.get("FANTASY_DATA_DIR"):
        return Path(env)
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "FantasyAgent"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "FantasyAgent"
    return Path.home() / ".local" / "share" / "fantasy-agent"


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open the database, creating it if missing. Transactions are explicit (see tx)."""
    path = path or data_dir() / "fantasy.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma journal_mode = wal")
    conn.execute("pragma foreign_keys = on")
    return conn


@contextmanager
def tx(conn: sqlite3.Connection):
    conn.execute("begin immediate")
    try:
        yield conn
    except BaseException:
        conn.execute("rollback")
        raise
    conn.execute("commit")


def migrate(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS) -> None:
    """Apply pending NNNN_name.sql files in order, each in one transaction."""
    version = conn.execute("pragma user_version").fetchone()[0]
    for file in sorted(migrations_dir.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        number = int(file.name[:4])
        if number <= version:
            continue
        try:
            conn.executescript(
                f"begin;\n{file.read_text(encoding='utf-8')}\npragma user_version = {number};\ncommit;"
            )
        except sqlite3.Error as e:
            if conn.in_transaction:
                conn.execute("rollback")
            raise RuntimeError(f"migration {file.name} failed: {e}") from e


# settings table (key/value, values are text; structured values are json)

def get_setting(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("select value from settings where key = ?", (key,)).fetchone()
    return row["value"] if row else default


def get_json(conn: sqlite3.Connection, key: str, default=None):
    value = get_setting(conn, key)
    return json.loads(value) if value is not None else default


def set_settings(conn: sqlite3.Connection, values: dict) -> None:
    """Store text values as is and everything else as json."""
    with tx(conn):
        for key, value in values.items():
            text = value if isinstance(value, str) else json.dumps(value)
            conn.execute(
                "insert into settings(key, value) values (?, ?) "
                "on conflict(key) do update set value = excluded.value",
                (key, text),
            )
