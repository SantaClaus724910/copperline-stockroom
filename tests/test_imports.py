"""CSV receipt intake: normalization, reference replay, and atomicity."""

from unittest import mock

from copperline import Conflict, NotFound, ValidationError
from copperline.service import Stockroom
from tests.helpers import DbCase

HEADER = "sku,bin_code,quantity,note\n"
ROW_A = "RES-10K,RACK-A,10,Delivery A\n"
ROW_B = "CAP-100N,DRAWER-B,8,Delivery B\n"


class ReceiptImportTests(DbCase):
    def setUp(self):
        super().setUp(); self.bootstrap()

    def test_import_two_rows(self):
        batch = self.app.import_receipts(self.csv_file(HEADER + ROW_A + ROW_B), "LOT-1")
        self.assertEqual((batch["row_count"], batch["replayed"]), (2, False))
        self.assertEqual(self.app.stock_by_part("RES-10K")["on_hand"], 10)
        self.assertEqual(self.app.stock_by_part("CAP-100N")["on_hand"], 8)
        self.assertEqual([e["batch_row"] for e in reversed(self.app.history())], [1, 2])
        self.assertTrue(self.app.health()["ok"])

    def test_import_replay_does_not_duplicate_events(self):
        path = self.csv_file(HEADER + ROW_A + ROW_B)
        first = self.app.import_receipts(path, "LOT-1")
        before = self.snapshot()
        second = self.app.import_receipts(path, "LOT-1")
        self.assertEqual(second, {**first, "replayed": True})
        self.assertEqual(self.snapshot(), before)

    def test_import_conflicting_reference_rejected(self):
        self.app.import_receipts(self.csv_file(HEADER + ROW_A), "LOT-1")
        before = self.snapshot()
        with self.assertRaises(Conflict):
            self.app.import_receipts(self.csv_file(HEADER + ROW_B, "other.csv"), "LOT-1")
        self.assertEqual(self.snapshot(), before)

    def test_replay_after_bin_deactivated(self):
        path = self.csv_file(HEADER + ROW_A)
        batch = self.app.import_receipts(path, "LOT-1")
        self.app.issue("RES-10K", "RACK-A", 10, "ISSUE-1", purpose="Kitting")
        self.app.set_active("bins", "RACK-A", False)
        before = self.snapshot()
        self.assertEqual(self.app.import_receipts(path, "LOT-1"), {**batch, "replayed": True})
        self.assertEqual(self.snapshot(), before)

    def test_import_with_bom(self):
        path = self.csv_file("\ufeff" + HEADER + ROW_A)
        self.assertEqual(self.app.import_receipts(path, "LOT-1")["row_count"], 1)

    def test_header_order_does_not_change_batch_identity(self):
        first = self.csv_file(HEADER + ROW_A)
        second = self.csv_file("quantity,note,bin_code,sku\n10,Delivery A,RACK-A,RES-10K\n", "reordered.csv")
        self.app.import_receipts(first, "LOT-1")
        self.assertTrue(self.app.import_receipts(second, "LOT-1")["replayed"])

    def test_whitespace_and_code_case_normalized(self):
        first = self.csv_file(HEADER + ROW_A)
        second = self.csv_file(HEADER + "res-10k,rack-a,010, Delivery A \n", "normalized.csv")
        self.app.import_receipts(first, "LOT-1")
        self.assertTrue(self.app.import_receipts(second, "LOT-1")["replayed"])

    def test_row_order_is_significant(self):
        self.app.import_receipts(self.csv_file(HEADER + ROW_A + ROW_B), "LOT-1")
        with self.assertRaises(Conflict):
            self.app.import_receipts(self.csv_file(HEADER + ROW_B + ROW_A, "swapped.csv"), "LOT-1")

    def test_invalid_second_row_rolls_back_first(self):
        path = self.csv_file(HEADER + ROW_A + "NO-SUCH-PART,RACK-A,7,Invalid\n")
        before = self.snapshot()
        with self.assertRaises(NotFound): self.app.import_receipts(path, "LOT-1")
        self.assertEqual(self.snapshot(), before)

    def test_invalid_bin_rolls_back_batch(self):
        path = self.csv_file(HEADER + ROW_A + "CAP-100N,NO-BIN,5,Invalid\n")
        before = self.snapshot()
        with self.assertRaises(NotFound): self.app.import_receipts(path, "LOT-1")
        self.assertEqual(self.snapshot(), before)

    def test_inactive_part_rejects_whole_batch(self):
        self.app.set_active("parts", "CAP-100N", False)
        before = self.snapshot()
        with self.assertRaises(Conflict): self.app.import_receipts(self.csv_file(HEADER + ROW_A + ROW_B), "LOT-1")
        self.assertEqual(self.snapshot(), before)

    def test_empty_csv(self):
        with self.assertRaises(ValidationError): self.app.import_receipts(self.csv_file(HEADER), "LOT-1")

    def test_missing_header(self):
        with self.assertRaises(ValidationError): self.app.import_receipts(self.csv_file("sku,quantity\nRES-10K,3\n"), "LOT-1")

    def test_duplicate_header(self):
        with self.assertRaises(ValidationError): self.app.import_receipts(self.csv_file("sku,bin_code,quantity,sku\nRES-10K,RACK-A,3,X\n"), "LOT-1")

    def test_invalid_integer_values(self):
        for field in ("-3", "2.5", "no", "0", "9223372036854775808"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.app.import_receipts(self.csv_file(HEADER + f"RES-10K,RACK-A,{field},Test\n"), "LOT-1")

    def test_csv_wrong_number_of_fields(self):
        for text in (HEADER + "RES-10K,RACK-A,10\n", HEADER + "RES-10K,RACK-A,10,Test,Extra\n"):
            with self.subTest(text=text), self.assertRaises(ValidationError):
                self.app.import_receipts(self.csv_file(text), "LOT-1")

    def test_repeated_sku_bin_rows_are_valid(self):
        batch = self.app.import_receipts(self.csv_file(HEADER + ROW_A + ROW_A), "LOT-1")
        self.assertEqual(batch["row_count"], 2)
        self.assertEqual(self.app.stock_by_part("RES-10K")["on_hand"], 20)

    def test_bad_utf8_rejected_without_writes(self):
        path = self.root / "bad.csv"
        path.write_bytes(b"\xff\xfe")
        before = self.snapshot()
        with self.assertRaises(ValidationError): self.app.import_receipts(path, "LOT-1")
        self.assertEqual(self.snapshot(), before)

    def test_file_size_limit(self):
        path = self.root / "big.csv"
        path.write_bytes(b"x" * 5_000_001)
        with self.assertRaises(ValidationError): self.app.import_receipts(path, "LOT-1")

    def test_row_count_limit(self):
        path = self.csv_file(HEADER + ROW_A * 5001)
        with self.assertRaises(ValidationError): self.app.import_receipts(path, "LOT-1")

    def test_batch_rows_are_distinct_from_single_references(self):
        self.app.receive("RES-10K", "RACK-A", 4, "LOT-1")
        batch = self.app.import_receipts(self.csv_file(HEADER + ROW_A), "LOT-1")
        self.assertFalse(batch["replayed"])
        self.assertEqual(self.app.stock_by_part("RES-10K")["on_hand"], 14)

    def test_injected_failure_after_first_row_rolls_back_everything(self):
        path = self.csv_file(HEADER + ROW_A + ROW_B)
        before = self.snapshot()
        actual = Stockroom._write_event
        seen = [0]

        def fail_after_write(service, con, request, payload_hash, **kwargs):
            result = actual(service, con, request, payload_hash, **kwargs)
            seen[0] += 1
            if seen[0] == 2:
                raise RuntimeError("injected second-row failure")
            return result

        with mock.patch.object(Stockroom, "_write_event", fail_after_write):
            with self.assertRaisesRegex(RuntimeError, "injected second-row failure"):
                self.app.import_receipts(path, "LOT-1")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.app.import_receipts(path, "LOT-1")["replayed"])

    def test_import_note_control_characters_rejected(self):
        with self.assertRaises(ValidationError):
            self.app.import_receipts(self.csv_file(HEADER + 'RES-10K,RACK-A,2,"line\nbreak"\n'), "LOT-1")
