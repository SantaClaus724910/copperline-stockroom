"""Disposable database and fixed-clock fixtures."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from copperline import Stockroom
from copperline.db import transaction


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 30, 9, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


class DbCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="copperline-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "stock.sqlite"
        self.clock = Clock()
        self.app = Stockroom(self.path, now=self.clock)

    def bootstrap(self):
        self.app.add_part("RES-10K", "10k resistor", reorder_point=5)
        self.app.add_part("CAP-100N", "100 nF capacitor", reorder_point=10)
        self.app.add_bin("RACK-A", "Passive component rack")
        self.app.add_bin("DRAWER-B", "Assembly drawer")

    def stock(self, sku="RES-10K", bin_code="RACK-A", qty=20, reference="R-1"):
        return self.app.receive(sku, bin_code, qty, reference)

    def snapshot(self):
        with transaction(self.path) as con:
            names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            return {table: [tuple(r) for r in con.execute(f'SELECT * FROM {table} ORDER BY rowid')] for table in names}

    def raw(self, sql, values=()):
        with transaction(self.path, write=True) as con:
            return con.execute(sql, values).rowcount

    def csv_file(self, contents: str, filename="receipts.csv") -> Path:
        path = self.root / filename
        path.write_text(contents, encoding="utf-8")
        return path
