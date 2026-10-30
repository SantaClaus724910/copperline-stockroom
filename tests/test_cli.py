"""Command parsing, JSON responses, exit status, and persistence."""

import contextlib
import io
import json
import subprocess
import sys
from unittest import mock

from copperline import Stockroom
from copperline.cli import main
from tests.helpers import DbCase


class CLITests(DbCase):
    def cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch("copperline.cli.Stockroom", side_effect=lambda db: Stockroom(db, now=self.clock)):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = main(["--db", str(self.path), *map(str, args)])
        return status, json.loads(stdout.getvalue()) if stdout.getvalue() else None, stderr.getvalue()

    def ok(self, *args):
        status, data, error = self.cli(*args)
        self.assertEqual((status, error), (0, ""), error)
        return data

    def test_init(self):
        result = self.ok("init")
        self.assertEqual(result["schema_version"], 1)

    def test_add_and_list_parts(self):
        self.ok("part", "add", "res-10k", "10k resistor", "--unit", "pcs", "--reorder", 10)
        self.assertEqual(self.ok("part", "list")[0]["sku"], "RES-10K")

    def test_add_and_list_bins(self):
        self.ok("bin", "add", "rack-a", "Main rack")
        self.assertEqual(self.ok("bin", "list")[0]["code"], "RACK-A")

    def test_stock_receipt_and_part_show(self):
        self.bootstrap()
        self.ok("stock", "receive", "RES-10K", "RACK-A", 5, "--ref", "R-1")
        self.assertEqual(self.ok("part", "show", "RES-10K")["on_hand"], 5)

    def test_stock_movement_round_trip(self):
        self.bootstrap()
        self.ok("stock", "receive", "RES-10K", "RACK-A", 20, "--ref", "R-1")
        self.ok("stock", "move", "RES-10K", "RACK-A", "DRAWER-B", 7, "--ref", "M-1")
        self.ok("stock", "issue", "RES-10K", "DRAWER-B", 3, "--ref", "I-1", "--purpose", "Workshop assembly")
        self.ok("stock", "count", "RES-10K", "RACK-A", 12, "--ref", "C-1", "--reason", "Recount")
        self.assertEqual(self.ok("part", "show", "RES-10K")["on_hand"], 16)
        self.assertEqual(len(self.ok("stock", "history", "--sku", "RES-10K")), 4)

    def test_duplicate_reference_replays(self):
        self.bootstrap()
        first = self.ok("stock", "receive", "RES-10K", "RACK-A", 20, "--ref", "R-1")
        self.assertFalse(first["replayed"])
        self.assertTrue(self.ok("stock", "receive", "RES-10K", "RACK-A", 20, "--ref", "R-1")["replayed"])

    def test_domain_error_exit_two(self):
        self.bootstrap()
        status, data, err = self.cli("stock", "issue", "RES-10K", "RACK-A", 1, "--ref", "I-1", "--purpose", "Build")
        self.assertEqual((status, data), (2, None))
        self.assertIn("Insufficient stock", err)

    def test_part_reorder_command(self):
        self.bootstrap()
        self.assertEqual(self.ok("part", "reorder", "RES-10K", 22)["reorder_point"], 22)

    def test_part_and_bin_activation_commands(self):
        self.bootstrap()
        self.assertEqual(self.ok("part", "deactivate", "RES-10K")["active"], 0)
        self.assertEqual(self.ok("part", "activate", "RES-10K")["active"], 1)
        self.assertEqual(self.ok("bin", "deactivate", "RACK-A")["active"], 0)
        self.assertEqual(self.ok("bin", "activate", "RACK-A")["active"], 1)

    def test_low_stock_and_inventory_reports(self):
        self.bootstrap()
        self.assertEqual({r["sku"] for r in self.ok("report", "low-stock")}, {"RES-10K", "CAP-100N"})
        self.assertEqual(self.ok("report", "inventory"), [])

    def test_import_receipt_file(self):
        self.bootstrap()
        source = self.csv_file("sku,bin_code,quantity,note\nRES-10K,RACK-A,12,Test\n")
        self.assertEqual(self.ok("import", "receipts", source, "--ref", "LOT-1")["row_count"], 1)
        self.assertTrue(self.ok("import", "receipts", source, "--ref", "LOT-1")["replayed"])

    def test_export_inventory(self):
        self.bootstrap(); self.stock(qty=7)
        path = self.root / "exports" / "stock.csv"
        self.assertEqual(self.ok("export", "inventory", "--output", path)["rows"], 1)
        self.assertTrue(path.exists())

    def test_unhealthy_health_report_returns_three(self):
        self.bootstrap(); self.stock(qty=5)
        self.raw("UPDATE balances SET quantity=3")
        status, data, err = self.cli("report", "health")
        self.assertEqual((status, data["ok"]), (3, False))

    def test_required_arguments_enforced_by_parser(self):
        for args in (("stock", "receive", "RES-10K"), ("stock", "issue", "RES-10K", "RACK-A", "2"),
                     ("import", "receipts", "file.csv"), ("part", "add")):
            with self.subTest(args=args), self.assertRaises(SystemExit):
                self.cli(*args)

    def test_unexpected_errors_not_silently_caught(self):
        with mock.patch("copperline.cli.dispatch", side_effect=RuntimeError("unexpected")):
            with self.assertRaisesRegex(RuntimeError, "unexpected"):
                self.cli("report", "inventory")

    def test_real_module_entrypoint(self):
        result = subprocess.run([sys.executable, "-m", "copperline", "--db", str(self.path), "init"],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["schema_version"], 1)

    def test_real_subprocess_persists_across_invocations(self):
        base = [sys.executable, "-m", "copperline", "--db", str(self.path)]
        for suffix in (("part", "add", "RES-10K", "Resistor"), ("bin", "add", "RACK-A", "Rack"),
                       ("stock", "receive", "RES-10K", "RACK-A", "7", "--ref", "R-1")):
            r = subprocess.run([*base, *suffix], capture_output=True, text=True, timeout=15)
            self.assertEqual(r.returncode, 0, r.stderr)
        shown = subprocess.run([*base, "part", "show", "RES-10K"], capture_output=True, text=True, timeout=15)
        self.assertEqual(json.loads(shown.stdout)["on_hand"], 7)

    def test_cli_uses_default_database_under_data(self):
        parser = __import__("copperline.cli", fromlist=["build_parser"]).build_parser()
        args = parser.parse_args(["init"])
        self.assertEqual(args.db, "data/stockroom.sqlite")
