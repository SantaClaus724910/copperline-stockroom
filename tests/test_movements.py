"""Ledger events, physical balances, and movement safety."""

from unittest import mock

from copperline import Conflict, NotFound, ValidationError
from copperline.service import Stockroom
from tests.helpers import DbCase


class MovementTests(DbCase):
    def setUp(self):
        super().setUp(); self.bootstrap()

    def test_receive_records_balance_and_event(self):
        event = self.stock(qty=32)
        self.assertEqual((event["kind"], event["quantity"], event["replayed"], event["reference"]),
                         ("receipt", 32, False, "R-1"))
        self.assertEqual(self.app.stock_by_part("RES-10K")["on_hand"], 32)

    def test_two_receipts_accumulate(self):
        self.stock(qty=10)
        self.stock(qty=30, reference="R-2")
        self.assertEqual(self.app.stock_by_part("RES-10K")["on_hand"], 40)

    def test_move_between_bins_preserves_total(self):
        self.stock(qty=50)
        move = self.app.move("RES-10K", "RACK-A", "DRAWER-B", 19, "MOVE-1")
        self.assertEqual((move["kind"], move["quantity"]), ("move", 19))
        self.assertEqual([(r["bin_code"], r["quantity"]) for r in self.app.stock_by_part("RES-10K")["locations"]],
                         [("DRAWER-B", 19), ("RACK-A", 31)])

    def test_issue_reduces_on_hand(self):
        self.stock(qty=30)
        self.app.issue("RES-10K", "RACK-A", 11, "BUILD-001", purpose="Prototype assembly")
        self.assertEqual(self.app.stock_by_part("RES-10K")["on_hand"], 19)

    def test_full_issue_results_in_zero_balance(self):
        self.stock(qty=8)
        self.app.issue("RES-10K", "RACK-A", 8, "BUILD-001", purpose="Breadboard")
        self.assertEqual(self.app.stock_by_part("RES-10K")["on_hand"], 0)
        self.assertEqual(self.app.stock_by_part("RES-10K")["locations"], [])

    def test_issue_requires_purpose(self):
        with self.assertRaises(ValidationError):
            self.app.issue("RES-10K", "RACK-A", 1, "BUILD-1", purpose=" ")

    def test_move_requires_distinct_bins(self):
        self.stock(qty=8)
        before = self.snapshot()
        with self.assertRaises(ValidationError):
            self.app.move("RES-10K", "RACK-A", "rack-a", 1, "MOVE-1")
        self.assertEqual(self.snapshot(), before)

    def test_move_without_stock_rejected(self):
        before = self.snapshot()
        with self.assertRaises(Conflict): self.app.move("RES-10K", "RACK-A", "DRAWER-B", 2, "MOVE-1")
        self.assertEqual(self.snapshot(), before)

    def test_insufficient_stock_rolls_back(self):
        self.stock(qty=3)
        before = self.snapshot()
        with self.assertRaises(Conflict): self.app.issue("RES-10K", "RACK-A", 4, "BUILD-1", purpose="Prototype")
        self.assertEqual(self.snapshot(), before)

    def test_oversized_move_is_all_or_nothing(self):
        self.stock(qty=3)
        before = self.snapshot()
        with self.assertRaises(Conflict): self.app.move("RES-10K", "RACK-A", "DRAWER-B", 4, "MOVE-1")
        self.assertEqual(self.snapshot(), before)

    def test_correct_count_downward(self):
        self.stock(qty=30)
        event = self.app.correct_count("RES-10K", "RACK-A", 26, "COUNT-1", reason="Physical stock count")
        self.assertEqual((event["kind"], event["quantity"], event["target_quantity"]), ("count_adjustment", -4, 26))
        self.assertEqual(self.app.stock_by_part("RES-10K")["on_hand"], 26)

    def test_correct_count_upward(self):
        self.stock(qty=30)
        event = self.app.correct_count("RES-10K", "RACK-A", 32, "COUNT-1", reason="Found misplaced components")
        self.assertEqual((event["quantity"], event["target_quantity"]), (2, 32))

    def test_correct_count_to_zero(self):
        self.stock(qty=2)
        self.app.correct_count("RES-10K", "RACK-A", 0, "COUNT-1", reason="Empty bin")
        self.assertEqual(self.app.stock_by_part("RES-10K")["on_hand"], 0)

    def test_correct_count_without_change_rejected(self):
        self.stock(qty=20)
        before = self.snapshot()
        with self.assertRaises(Conflict): self.app.correct_count("RES-10K", "RACK-A", 20, "COUNT-1", reason="Count")
        self.assertEqual(self.snapshot(), before)

    def test_correct_count_requires_reason(self):
        with self.assertRaises(ValidationError):
            self.app.correct_count("RES-10K", "RACK-A", 1, "COUNT-1", reason="")

    def test_rejected_quantities(self):
        for bad in (0, -1, True, 2.0, "2", 2**63):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.app.receive("RES-10K", "RACK-A", bad, "R-2")

    def test_unknown_part_and_bins(self):
        with self.assertRaises(NotFound): self.app.receive("MISSING", "RACK-A", 2, "R-2")
        with self.assertRaises(NotFound): self.app.receive("RES-10K", "MISSING", 2, "R-2")

    def test_inactive_bin_rejected(self):
        self.app.set_active("bins", "RACK-A", False)
        before = self.snapshot()
        with self.assertRaises(Conflict): self.app.receive("RES-10K", "RACK-A", 2, "R-2")
        self.assertEqual(self.snapshot(), before)

    def test_repeated_reference_is_idempotent(self):
        first = self.stock(qty=10)
        before = self.snapshot()
        replay = self.stock(qty=10)
        self.assertEqual(replay, {**first, "replayed": True})
        self.assertEqual(self.snapshot(), before)

    def test_conflicting_reference_rejected(self):
        self.stock(qty=10)
        before = self.snapshot()
        with self.assertRaises(Conflict): self.stock(qty=9)
        self.assertEqual(self.snapshot(), before)

    def test_reference_global_across_event_kinds(self):
        self.stock(qty=20)
        before = self.snapshot()
        with self.assertRaises(Conflict): self.app.issue("RES-10K", "RACK-A", 1, "R-1", purpose="Build")
        self.assertEqual(self.snapshot(), before)

    def test_replay_after_stock_changes(self):
        first = self.stock(qty=10)
        self.app.issue("RES-10K", "RACK-A", 10, "BUILD-1", purpose="Build")
        before = self.snapshot()
        self.assertEqual(self.stock(qty=10), {**first, "replayed": True})
        self.assertEqual(self.snapshot(), before)

    def test_replay_after_catalog_deactivation(self):
        first = self.stock(qty=4)
        self.app.issue("RES-10K", "RACK-A", 4, "BUILD-1", purpose="Assembly")
        self.app.set_active("parts", "RES-10K", False)
        before = self.snapshot()
        self.assertEqual(self.stock(qty=4), {**first, "replayed": True})
        self.assertEqual(self.snapshot(), before)

    def test_note_part_of_retry_identity(self):
        self.app.receive("RES-10K", "RACK-A", 4, "R-1", note="Crate A")
        with self.assertRaises(Conflict): self.app.receive("RES-10K", "RACK-A", 4, "R-1", note="Crate B")

    def test_uppercase_code_normalization_allows_replay(self):
        original = self.app.receive("res-10k", "rack-a", 10, "R-1")
        self.assertEqual(self.app.receive("RES-10K", "RACK-A", 10, "R-1"), {**original, "replayed": True})

    def test_count_replay_does_not_reapply_delta(self):
        self.stock(qty=10)
        first = self.app.correct_count("RES-10K", "RACK-A", 7, "COUNT-1", reason="Recount")
        self.app.issue("RES-10K", "RACK-A", 2, "BUILD-1", purpose="Assembly")
        before = self.snapshot()
        self.assertEqual(self.app.correct_count("RES-10K", "RACK-A", 7, "COUNT-1", reason="Recount"),
                         {**first, "replayed": True})
        self.assertEqual(self.snapshot(), before)

    def test_quantity_limit_overflow_rejected(self):
        self.stock(qty=2**63-1)
        before = self.snapshot()
        with self.assertRaises(Conflict): self.app.receive("RES-10K", "RACK-A", 1, "R-2")
        self.assertEqual(self.snapshot(), before)

    def test_mid_move_failure_rolls_back_source_debit(self):
        self.stock(qty=20)
        before = self.snapshot()
        real = Stockroom._delta.__func__
        calls = [0]

        def fail_second(cls, con, part_id, bin_id, delta):
            calls[0] += 1
            result = real(cls, con, part_id, bin_id, delta)
            if calls[0] == 2:
                raise RuntimeError("injected failure after destination write")
            return result

        with mock.patch.object(Stockroom, "_delta", classmethod(fail_second)):
            with self.assertRaisesRegex(RuntimeError, "injected failure"):
                self.app.move("RES-10K", "RACK-A", "DRAWER-B", 5, "MOVE-1")
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(self.app.health()["ok"])

    def test_clock_failure_rolls_back_movement(self):
        before = self.snapshot()
        with mock.patch.object(self.clock, "value", "invalid"):
            with self.assertRaises(ValidationError): self.stock()
        self.assertEqual(self.snapshot(), before)

    def test_history_reports_bins_and_reverse_order(self):
        self.stock(qty=50)
        self.app.move("RES-10K", "RACK-A", "DRAWER-B", 15, "MOVE-1")
        self.app.issue("RES-10K", "DRAWER-B", 2, "BUILD-1", purpose="Assembly")
        rows = self.app.history(sku="RES-10K")
        self.assertEqual([r["kind"] for r in rows], ["issue", "move", "receipt"])
        self.assertEqual((rows[0]["from_bin_code"], rows[1]["to_bin_code"]), ("DRAWER-B", "DRAWER-B"))

    def test_history_limit(self):
        for n in range(5): self.stock(qty=1, reference=f"R-{n}")
        self.assertEqual(len(self.app.history(limit=2)), 2)
        with self.assertRaises(ValidationError): self.app.history(limit=1001)
