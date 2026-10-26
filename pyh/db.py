from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Iterator

from .config import Config, ROOT


class ClosingConnection(sqlite3.Connection):
    """A sqlite connection that also closes when used as a context manager."""
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect(path: str | None = None) -> sqlite3.Connection:
    target = path or Config.DATABASE_PATH
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(target, timeout=15, isolation_level=None, factory=ClosingConnection)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 15000")
    return con


@contextlib.contextmanager
def transaction(path: str | None = None) -> Iterator[sqlite3.Connection]:
    con = connect(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db(path: str | None = None) -> None:
    migration = ROOT / "migrations" / "001_init.sql"
    with connect(path) as con:
        con.executescript(migration.read_text(encoding="utf-8"))
        con.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES('trial_days',?)", (str(Config.TRIAL_DAYS),))
        con.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES('public_demo_enabled',?)", ("1" if Config.PUBLIC_DEMO_ENABLED else "0",))


def one(con: sqlite3.Connection, sql: str, params: tuple = ()):
    return con.execute(sql, params).fetchone()


def all_(con: sqlite3.Connection, sql: str, params: tuple = ()):
    return con.execute(sql, params).fetchall()
