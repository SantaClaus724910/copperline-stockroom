"""Schema checks and file persistence across service instances."""

import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from unittest import mock

from copperline import Stockroom, ValidationError
from copperline.db import SCHEMA_VERSION, initialize, transaction
from tests.helpers import DbCase


class PersistenceTests(DbCase):
    def test_schema_version_one(self):
        with transaction(self.path) as con:
            self.assertEqual(con.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()[0], str(SCHEMA_VERSION))

    def test_init_is_idempotent(self):
        self.assertFalse(initialize(self.path)["created"])
        self.assertEqual(Stockroom(self.path).schema_status["schema_version"], SCHEMA_VERSION)

    def test_reopen_preserves_every_table(self):
        self.bootstrap(); self.stock()
        before = self.snapshot()
        Stockroom(self.path)
        self.assertEqual(self.snapshot(), before)

    def test_reopen_with_new_client_sees_stock(self):
        self.bootstrap(); self.stock(qty=17)
        other = Stockroom(self.path)
        self.assertEqual(other.stock_by_part("RES-10K")["on_hand"], 17)
        self.assertTrue(other.health()["ok"])

    def test_reopen_does_not_reset_reference_identity(self):
        self.bootstrap(); original = self.stock(qty=17)
        other = Stockroom(self.path)
        self.assertEqual(other.receive("RES-10K", "RACK-A", 17, "R-1"), {**original, "replayed": True})

    def test_unknown_future_schema_refused_unchanged(self):
        self.raw("UPDATE schema_meta SET value='99' WHERE key='version'")
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "Unsupported schema version"):
            Stockroom(self.path)
        self.assertEqual(self.snapshot(), before)

    def test_missing_schema_marker_refused(self):
        self.raw("DELETE FROM schema_meta")
        before = self.snapshot()
        with self.assertRaises(RuntimeError): Stockroom(self.path)
        self.assertEqual(self.snapshot(), before)

    def test_unrelated_database_not_overwritten(self):
        path = self.root / "other.sqlite"
        with transaction(path, write=True) as con:
            con.execute("CREATE TABLE external_data (name TEXT)")
            con.execute("INSERT INTO external_data VALUES ('do not change')")
        with self.assertRaises(RuntimeError): Stockroom(path)
        with transaction(path) as con:
            self.assertEqual(con.execute("SELECT name FROM external_data").fetchone()[0], "do not change")

    def test_foreign_keys_enabled_on_every_connection(self):
        with transaction(self.path) as con:
            self.assertEqual(con.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_rollback_on_arbitrary_exception(self):
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "simulated crash"):
            with transaction(self.path, write=True) as con:
                con.execute("INSERT INTO bins(code,description,created_utc) VALUES ('B-1','Bin','now')")
                raise RuntimeError("simulated crash")
        self.assertEqual(self.snapshot(), before)

    def test_unaware_clock_rejected_without_writes(self):
        app = Stockroom(self.path, now=lambda: datetime(2026, 10, 30))
        before = self.snapshot()
        with self.assertRaises(ValidationError): app.add_bin("RACK-A", "Rack")
        self.assertEqual(self.snapshot(), before)

    def test_schema_does_not_allow_negative_balance(self):
        self.bootstrap(); self.stock(qty=2)
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw("UPDATE balances SET quantity=-1")

    def test_database_file_is_created_in_nested_directory(self):
        path = self.root / "storage" / "nested" / "stock.sqlite"
        self.assertTrue(Stockroom(path).schema_status["created"])
        self.assertTrue(path.exists())

    def test_cli_help_does_not_create_database(self):
        path = self.root / "absent.sqlite"
        result = subprocess.run([sys.executable, "-m", "copperline", "--db", str(path), "--help"],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(path.exists())

    def test_package_files_present(self):
        root = Path(__file__).resolve().parents[1]
        for item in ("README.md", "ROADMAP.md", "LICENSE", ".gitignore", "pyproject.toml", "examples/receipts.csv"):
            with self.subTest(item=item): self.assertTrue((root / item).is_file())
