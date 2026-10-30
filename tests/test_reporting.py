"""Stock snapshot reporting, ledger reconciliation, and safe CSV exports."""

import csv
import sqlite3
from pathlib import Path
from unittest import mock

from copperline import NotFound, ValidationError
from tests.helpers import DbCase


class ReportingTests(DbCase):
    def setUp(self):
        super().setUp(); self.bootstrap()

    def test_empty_inventory_report(self):
        self.assertEqual(self.app.inventory(), [])

    def test_inventory_report_has_positive_rows_only(self):
        self.stock(qty=15)
        self.app.issue("RES-10K", "RACK-A", 15, "ISSUE-1", purpose="Assembly")
        self.assertEqual(self.app.inventory(), [])

    def test_inventory_sorted_by_sku_and_bin(self):
        self.stock(qty=15)
        self.app.receive("CAP-100N", "DRAWER-B", 8, "R-2")
        self.assertEqual([(r["sku"], r["bin_code"]) for r in self.app.inventory()],
                         [("CAP-100N", "DRAWER-B"), ("RES-10K", "RACK-A")])

    def test_low_stock_shows_parts_below_reorder_point(self):
        self.app.receive("CAP-100N", "DRAWER-B", 3, "R-1")
        self.assertEqual({r["sku"] for r in self.app.low_stock()}, {"CAP-100N", "RES-10K"})

    def test_low_stock_includes_threshold_equal(self):
        self.stock(qty=5)
        self.assertIn("RES-10K", [r["sku"] for r in self.app.low_stock()])

    def test_low_stock_uses_total_across_bins(self):
        self.stock(qty=15)
        self.app.move("RES-10K", "RACK-A", "DRAWER-B", 8, "MOVE-1")
        self.assertNotIn("RES-10K", [r["sku"] for r in self.app.low_stock()])

    def test_low_stock_excludes_inactive_parts(self):
        self.app.set_active("parts", "RES-10K", False)
        self.assertNotIn("RES-10K", [r["sku"] for r in self.app.low_stock()])

    def test_low_stock_excludes_threshold_zero(self):
        self.app.add_part("DIODE-1N", "Diode")
        self.assertNotIn("DIODE-1N", [r["sku"] for r in self.app.low_stock()])

    def test_stock_by_part_includes_location_descriptions(self):
        self.stock(qty=2)
        result = self.app.stock_by_part("RES-10K")
        self.assertEqual(result["locations"], [{"bin_code": "RACK-A", "description": "Passive component rack", "quantity": 2}])
        with self.assertRaises(NotFound): self.app.stock_by_part("NOT-FOUND")

    def test_health_ok_for_complex_stock_flow(self):
        self.stock(qty=50)
        self.app.move("RES-10K", "RACK-A", "DRAWER-B", 10, "MOVE-1")
        self.app.issue("RES-10K", "DRAWER-B", 2, "BUILD-1", purpose="Assembly")
        self.app.correct_count("RES-10K", "RACK-A", 39, "COUNT-1", reason="Recount")
        self.assertEqual(self.app.health(), {"ok": True, "sqlite_integrity": ["ok"], "foreign_key_violations": [],
                                            "balance_disagreements": [], "import_disagreements": []})

    def test_health_catches_balance_tampering_without_repair(self):
        self.stock(qty=10)
        self.raw("UPDATE balances SET quantity=12")
        before = self.snapshot()
        h = self.app.health()
        self.assertFalse(h["ok"])
        self.assertEqual(h["balance_disagreements"][0]["ledger_quantity"], 10)
        self.assertEqual(self.snapshot(), before)

    def test_health_catches_deleted_balance(self):
        self.stock(qty=10)
        self.raw("DELETE FROM balances")
        self.assertFalse(self.app.health()["ok"])
        self.assertIsNone(self.app.health()["balance_disagreements"][0]["stored_quantity"])

    def test_health_catches_import_row_count_tampering(self):
        source = self.csv_file("sku,bin_code,quantity,note\nRES-10K,RACK-A,2,Test\n")
        self.app.import_receipts(source, "LOT-1")
        self.raw("UPDATE import_batches SET row_count=2")
        self.assertEqual(self.app.health()["import_disagreements"][0]["actual_rows"], 1)

    def test_foreign_key_constraints_enabled(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.raw("INSERT INTO balances(part_id,bin_id,quantity) VALUES (999,999,1)")

    def test_export_creates_csv_with_real_rows(self):
        self.stock(qty=5)
        path = self.root / "out" / "inventory.csv"
        info = self.app.export_inventory(path)
        self.assertEqual(info["rows"], 1)
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual((rows[0]["sku"], rows[0]["quantity"]), ("RES-10K", "5"))

    def test_export_escapes_spreadsheet_formula_text(self):
        self.app.add_part("DIODE-1N", "=HYPERLINK(\"http://example.invalid\")")
        self.app.receive("DIODE-1N", "RACK-A", 1, "R-1")
        self.app.export_inventory(self.root / "out.csv")
        with (self.root / "out.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertTrue(rows[0]["description"].startswith("'="))
        self.assertEqual(self.app.stock_by_part("DIODE-1N")["description"][0], "=")

    def test_export_atomically_preserves_previous_file_on_error(self):
        self.stock(qty=5)
        path = self.root / "inventory.csv"
        path.write_text("previous report\n", encoding="utf-8")
        with mock.patch.object(self.app, "_csv_safe", side_effect=RuntimeError("broken serialization")):
            with self.assertRaisesRegex(RuntimeError, "broken serialization"):
                self.app.export_inventory(path)
        self.assertEqual(path.read_text(encoding="utf-8"), "previous report\n")
        self.assertEqual(list(self.root.glob(".copperline-*")), [])

    def test_export_does_not_modify_database(self):
        self.stock(qty=5)
        before = self.snapshot()
        self.app.export_inventory(self.root / "out.csv")
        self.assertEqual(self.snapshot(), before)

    def test_history_rejects_bad_limit(self):
        for value in (0, 1001, True):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.app.history(limit=value)
