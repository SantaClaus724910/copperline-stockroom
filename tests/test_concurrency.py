"""Concurrent operations use separate SQLite connections with serialized writes."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from copperline import Conflict, Stockroom
from tests.helpers import DbCase


class ConcurrencyTests(DbCase):
    def setUp(self):
        super().setUp(); self.bootstrap()
        self.other = Stockroom(self.path, now=self.clock)

    @staticmethod
    def race(*operations):
        barrier = Barrier(len(operations))

        def call(fn):
            barrier.wait(timeout=5)
            try:
                return "ok", fn()
            except Conflict as exc:
                return "conflict", str(exc)

        with ThreadPoolExecutor(max_workers=len(operations)) as executor:
            futures = [executor.submit(call, fn) for fn in operations]
            return [future.result(timeout=20) for future in futures]

    def test_competing_receipt_same_reference_is_replayed(self):
        results = self.race(lambda: self.app.receive("RES-10K", "RACK-A", 10, "REF-1"),
                            lambda: self.other.receive("RES-10K", "RACK-A", 10, "REF-1"))
        self.assertEqual([r[0] for r in results], ["ok", "ok"])
        self.assertCountEqual([r[1]["replayed"] for r in results], [False, True])
        self.assertEqual(self.app.stock_by_part("RES-10K")["on_hand"], 10)

    def test_competing_conflicting_references_choose_one(self):
        results = self.race(lambda: self.app.receive("RES-10K", "RACK-A", 10, "REF-1"),
                            lambda: self.other.receive("RES-10K", "RACK-A", 9, "REF-1"))
        self.assertCountEqual([r[0] for r in results], ["ok", "conflict"])
        self.assertEqual(len(self.app.history()), 1)

    def test_two_different_receipts_both_apply(self):
        results = self.race(lambda: self.app.receive("RES-10K", "RACK-A", 10, "REF-1"),
                            lambda: self.other.receive("RES-10K", "RACK-A", 12, "REF-2"))
        self.assertEqual([r[0] for r in results], ["ok", "ok"])
        self.assertEqual(self.app.stock_by_part("RES-10K")["on_hand"], 22)

    def test_two_issues_cannot_overspend_balance(self):
        self.stock(qty=10)
        results = self.race(lambda: self.app.issue("RES-10K", "RACK-A", 8, "ISS-1", purpose="Assembly"),
                            lambda: self.other.issue("RES-10K", "RACK-A", 8, "ISS-2", purpose="Assembly"))
        self.assertCountEqual([r[0] for r in results], ["ok", "conflict"])
        self.assertEqual(self.app.stock_by_part("RES-10K")["on_hand"], 2)
        self.assertTrue(self.app.health()["ok"])

    def test_move_and_issue_cannot_double_spend(self):
        self.stock(qty=10)
        results = self.race(lambda: self.app.move("RES-10K", "RACK-A", "DRAWER-B", 8, "MOVE-1"),
                            lambda: self.other.issue("RES-10K", "RACK-A", 8, "ISS-1", purpose="Assembly"))
        self.assertCountEqual([r[0] for r in results], ["ok", "conflict"])
        self.assertEqual(self.app.stock_by_part("RES-10K")["on_hand"], 2 if results[0][0] == "conflict" else 10)
        self.assertTrue(self.app.health()["ok"])

    def test_concurrent_import_batch_has_one_commit(self):
        source = self.csv_file("sku,bin_code,quantity,note\nRES-10K,RACK-A,5,Test\n")
        results = self.race(lambda: self.app.import_receipts(source, "LOT-1"),
                            lambda: self.other.import_receipts(source, "LOT-1"))
        self.assertEqual([r[0] for r in results], ["ok", "ok"])
        self.assertCountEqual([r[1]["replayed"] for r in results], [False, True])
        self.assertEqual(self.app.stock_by_part("RES-10K")["on_hand"], 5)

    def test_simultaneous_initialization_safe(self):
        path = self.root / "parallel.sqlite"
        barrier = Barrier(5)

        def init():
            barrier.wait(timeout=5)
            return Stockroom(path).schema_status

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(init) for _ in range(5)]
            results = [f.result(timeout=20) for f in futures]
        self.assertEqual(sum(1 for r in results if r["created"]), 1)
        self.assertTrue(Stockroom(path).health()["ok"])

    def test_concurrent_history_and_receipts_consistent(self):
        self.stock(qty=5)
        results = self.race(lambda: self.app.history(sku="RES-10K"),
                            lambda: self.other.receive("RES-10K", "RACK-A", 5, "R-2"))
        self.assertEqual([r[0] for r in results], ["ok", "ok"])
        self.assertTrue(self.app.health()["ok"])
