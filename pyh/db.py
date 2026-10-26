"""Database access for local SQLite and Turso's serverless SQLite-compatible database.

On Vercel, set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN so users, sessions and
projects are shared across function instances. Local development continues to
use the standard-library sqlite3 driver.
"""
from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Mapping, Iterator
from pathlib import Path
from typing import Any

from .config import Config, ROOT


class ClosingConnection(sqlite3.Connection):
    """A sqlite connection that also closes when used as a context manager."""
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class _RemoteRow(dict):
    """Dictionary row with SQLite-row-style integer indexing."""
    def __init__(self, names: list[str], values: Any):
        if isinstance(values, Mapping):
            data = dict(values)
        else:
            data = dict(zip(names, values))
        super().__init__(data)
        self._ordered_values = list(data.values())

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._ordered_values[key]
        return super().__getitem__(key)


class _RemoteCursor:
    """Adapt a DB-API cursor to sqlite3.Cursor's row behavior."""
    def __init__(self, cursor):
        self._cursor = cursor

    @property
    def description(self):
        return self._cursor.description

    @property
    def rowcount(self):
        return getattr(self._cursor, "rowcount", -1)

    @property
    def lastrowid(self):
        return getattr(self._cursor, "lastrowid", None)

    def _convert(self, row):
        if row is None:
            return None
        if isinstance(row, Mapping) or isinstance(row, _RemoteRow):
            return row
        description = self.description or ()
        names = [col[0] if isinstance(col, (tuple, list)) else getattr(col, "name", str(col)) for col in description]
        return _RemoteRow(names, row)

    def fetchone(self):
        return self._convert(self._cursor.fetchone())

    def fetchall(self):
        return [self._convert(row) for row in self._cursor.fetchall()]

    def fetchmany(self, size=None):
        rows = self._cursor.fetchmany() if size is None else self._cursor.fetchmany(size)
        return [self._convert(row) for row in rows]

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                break
            yield row


class _RemoteConnection:
    """A small sqlite3-compatible wrapper around turso_serverless DB-API."""
    row_factory = None

    def __init__(self, connection):
        self._connection = connection

    @staticmethod
    def _raise_sqlite_error(exc):
        if isinstance(exc, sqlite3.Error):
            raise exc
        message = str(exc)
        low = message.lower()
        if any(term in low for term in ("unique constraint", "constraint failed", "foreign key constraint", "primary key constraint")):
            raise sqlite3.IntegrityError(message) from exc
        if any(term in low for term in ("no such table", "no such column", "syntax error", "database is locked", "no such index")):
            raise sqlite3.OperationalError(message) from exc
        raise sqlite3.DatabaseError(message) from exc

    def execute(self, sql: str, parameters=()):
        try:
            return _RemoteCursor(self._connection.execute(sql, parameters))
        except Exception as exc:
            self._raise_sqlite_error(exc)

    def executemany(self, sql: str, seq_of_parameters):
        try:
            return _RemoteCursor(self._connection.executemany(sql, seq_of_parameters))
        except Exception as exc:
            self._raise_sqlite_error(exc)

    def executescript(self, sql_script: str):
        """Execute a migration script statement by statement over HTTP."""
        statement = ""
        for char in sql_script:
            statement += char
            if char == ";" and sqlite3.complete_statement(statement):
                sql = statement.strip()
                statement = ""
                if not sql:
                    continue
                # PRAGMA settings are connection-local SQLite options and do not
                # apply to the remote database endpoint.
                if sql.lstrip().upper().startswith("PRAGMA "):
                    continue
                self.execute(sql)
        if statement.strip():
            if sqlite3.complete_statement(statement + ";"):
                sql = statement.strip()
                if not sql.lstrip().upper().startswith("PRAGMA "):
                    self.execute(sql)
            else:
                raise sqlite3.OperationalError("Incomplete SQL in migration script.")

    def commit(self):
        try:
            return self._connection.commit()
        except Exception as exc:
            self._raise_sqlite_error(exc)

    def rollback(self):
        try:
            return self._connection.rollback()
        except Exception as exc:
            self._raise_sqlite_error(exc)

    def close(self):
        try:
            return self._connection.close()
        except Exception as exc:
            self._raise_sqlite_error(exc)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()
        return False


def _remote_database_configured() -> bool:
    return bool(Config.TURSO_DATABASE_URL and Config.TURSO_AUTH_TOKEN)


def connect(path: str | None = None):
    # An explicit local path always wins (used by tests, CLI migration and local dev).
    # A None path opts into the shared remote database when configured.
    if path is None and _remote_database_configured():
        try:
            import turso_serverless
        except ImportError as exc:
            raise RuntimeError(
                "Turso is configured, but the turso-serverless dependency is missing. "
                "Install the dependencies from requirements.txt and redeploy."
            ) from exc
        conn = turso_serverless.connect(
            Config.TURSO_DATABASE_URL,
            auth_token=Config.TURSO_AUTH_TOKEN,
        )
        return _RemoteConnection(conn)

    target = path or Config.DATABASE_PATH
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(target, timeout=15, isolation_level=None, factory=ClosingConnection)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 15000")
    return con


@contextlib.contextmanager
def transaction(path: str | None = None) -> Iterator[Any]:
    con = connect(path)
    try:
        # BEGIN works for local SQLite and Turso's remote DB-API connection.
        con.execute("BEGIN")
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
        # Avoid replaying dozens of schema statements on every Vercel cold start.
        # The marker is written only after the complete migration succeeds, so an
        # interrupted first run safely retries the idempotent CREATE statements.
        con.execute("CREATE TABLE IF NOT EXISTS pyh_schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))) ")
        applied = con.execute("SELECT version FROM pyh_schema_migrations WHERE version=?", ("001_init.sql",)).fetchone()
        if not applied:
            con.executescript(migration.read_text(encoding="utf-8"))
            con.execute("INSERT OR IGNORE INTO pyh_schema_migrations(version) VALUES(?)", ("001_init.sql",))
        con.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES('trial_days',?)", (str(Config.TRIAL_DAYS),))
        con.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES('public_demo_enabled',?)", ("1" if Config.PUBLIC_DEMO_ENABLED else "0",))


def one(con, sql: str, params: tuple = ()):
    return con.execute(sql, params).fetchone()


def all_(con, sql: str, params: tuple = ()):
    return con.execute(sql, params).fetchall()
