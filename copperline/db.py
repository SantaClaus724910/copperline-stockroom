"""SQLite schema and unit-of-work transaction management."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 1
SCHEMA = (
    "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE parts (
        id INTEGER PRIMARY KEY,
        sku TEXT NOT NULL UNIQUE,
        description TEXT NOT NULL,
        unit TEXT NOT NULL,
        reorder_point INTEGER NOT NULL DEFAULT 0 CHECK(reorder_point>=0),
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
        created_utc TEXT NOT NULL
    )""",
    """CREATE TABLE bins (
        id INTEGER PRIMARY KEY,
        code TEXT NOT NULL UNIQUE,
        description TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
        created_utc TEXT NOT NULL
    )""",
    """CREATE TABLE balances (
        part_id INTEGER NOT NULL REFERENCES parts(id),
        bin_id INTEGER NOT NULL REFERENCES bins(id),
        quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity>=0),
        PRIMARY KEY(part_id,bin_id)
    )""",
    """CREATE TABLE import_batches (
        id INTEGER PRIMARY KEY,
        reference TEXT NOT NULL UNIQUE,
        payload_hash TEXT NOT NULL,
        row_count INTEGER NOT NULL CHECK(row_count>0),
        created_utc TEXT NOT NULL
    )""",
    """CREATE TABLE stock_events (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL CHECK(kind IN ('receipt','move','issue','count_adjustment')),
        part_id INTEGER NOT NULL REFERENCES parts(id),
        from_bin_id INTEGER REFERENCES bins(id),
        to_bin_id INTEGER REFERENCES bins(id),
        quantity INTEGER NOT NULL,
        target_quantity INTEGER,
        reference TEXT UNIQUE,
        batch_id INTEGER REFERENCES import_batches(id),
        batch_row INTEGER,
        note TEXT NOT NULL DEFAULT '',
        payload_hash TEXT NOT NULL,
        created_utc TEXT NOT NULL,
        UNIQUE(batch_id,batch_row),
        CHECK ((reference IS NOT NULL AND batch_id IS NULL AND batch_row IS NULL)
            OR (reference IS NULL AND batch_id IS NOT NULL AND batch_row > 0)),
        CHECK ((kind='receipt' AND quantity>0 AND from_bin_id IS NULL AND to_bin_id IS NOT NULL AND target_quantity IS NULL)
            OR (kind='move' AND quantity>0 AND from_bin_id IS NOT NULL AND to_bin_id IS NOT NULL
                AND from_bin_id<>to_bin_id AND target_quantity IS NULL)
            OR (kind='issue' AND quantity>0 AND from_bin_id IS NOT NULL AND to_bin_id IS NULL AND target_quantity IS NULL)
            OR (kind='count_adjustment' AND quantity<>0 AND from_bin_id IS NULL AND to_bin_id IS NOT NULL
                AND target_quantity>=0))
    )""",
    """CREATE TABLE catalog_events (
        id INTEGER PRIMARY KEY,
        entity_type TEXT NOT NULL CHECK(entity_type IN ('part','bin')),
        entity_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        details TEXT NOT NULL,
        created_utc TEXT NOT NULL
    )""",
    "CREATE INDEX stock_part_time ON stock_events(part_id,id)",
    "CREATE INDEX stock_to_bin ON stock_events(to_bin_id,part_id)",
    "CREATE INDEX stock_from_bin ON stock_events(from_bin_id,part_id)",
    "CREATE INDEX stock_batch ON stock_events(batch_id,batch_row)",
)
REQUIRED_COLUMNS = {
    "parts": {"id", "sku", "description", "unit", "reorder_point", "active", "created_utc"},
    "bins": {"id", "code", "description", "active", "created_utc"},
    "balances": {"part_id", "bin_id", "quantity"},
    "import_batches": {"id", "reference", "payload_hash", "row_count", "created_utc"},
    "stock_events": {"id", "kind", "part_id", "from_bin_id", "to_bin_id", "quantity", "reference", "batch_id", "batch_row", "target_quantity", "payload_hash", "created_utc", "note"},
    "catalog_events": {"id", "entity_type", "entity_id", "action", "details", "created_utc"},
    "schema_meta": {"key", "value"},
}


def connect(path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), isolation_level=None, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=10000")
    return con


@contextmanager
def transaction(path: str | Path, *, write: bool = False) -> Iterator[sqlite3.Connection]:
    con = connect(path)
    try:
        con.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def initialize(path: str | Path) -> dict:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with transaction(path, write=True) as con:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        created = not names
        if created:
            for statement in SCHEMA:
                con.execute(statement)
            con.execute("INSERT INTO schema_meta(key,value) VALUES ('version',?)", (str(SCHEMA_VERSION),))
        else:
            if "schema_meta" not in names:
                raise RuntimeError("Unrecognized database; no changes made")
            row = con.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()
            if row is None or row[0] != str(SCHEMA_VERSION):
                raise RuntimeError(f"Unsupported schema version: {row[0] if row else 'missing'}")
        for table, columns in REQUIRED_COLUMNS.items():
            actual = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
            if not columns <= actual:
                raise RuntimeError(f"Schema mismatch: {table}; no changes made")
        if created and con.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("Schema verification failed")
    return {"created": created, "schema_version": SCHEMA_VERSION}
