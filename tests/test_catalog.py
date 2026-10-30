"""Catalog registration, activation, and reorder-point tests."""

from copperline import Conflict, NotFound, ValidationError
from tests.helpers import DbCase


class CatalogTests(DbCase):
    def test_add_part_normalizes_code_and_preserves_unit(self):
        part = self.app.add_part("res-10k", "  10k resistor  ", unit="pcs", reorder_point=7)
        self.assertEqual((part["sku"], part["description"], part["unit"], part["reorder_point"]),
                         ("RES-10K", "10k resistor", "pcs", 7))

    def test_part_defaults(self):
        part = self.app.add_part("CAP-100N", "Capacitor")
        self.assertEqual((part["active"], part["unit"], part["reorder_point"]), (1, "pcs", 0))

    def test_add_bin_normalizes_code(self):
        result = self.app.add_bin("rack-a", "  Main rack ")
        self.assertEqual((result["code"], result["description"]), ("RACK-A", "Main rack"))

    def test_duplicate_part_rejected(self):
        self.app.add_part("RES-10K", "Resistor")
        with self.assertRaises(Conflict): self.app.add_part("res-10k", "Another")

    def test_duplicate_bin_rejected(self):
        self.app.add_bin("RACK-A", "Rack")
        with self.assertRaises(Conflict): self.app.add_bin("rack-a", "Other rack")

    def test_invalid_sku_and_bin_codes(self):
        for bad in ("x", "hello there", "@x", "", "!", "x" * 49):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.app.add_part(bad, "Part")
        with self.assertRaises(ValidationError): self.app.add_bin("+BIN", "Rack")

    def test_invalid_descriptions_and_units(self):
        with self.assertRaises(ValidationError): self.app.add_part("RES-10K", "   ")
        with self.assertRaises(ValidationError): self.app.add_part("RES-10K", "Name\nInvalid")
        with self.assertRaises(ValidationError): self.app.add_part("RES-10K", "Valid", unit=" ")

    def test_reorder_point_must_be_nonnegative_integer(self):
        for bad in (-1, 3.5, True, "3"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.app.add_part("RES-10K", "Resistor", reorder_point=bad)

    def test_list_catalog_sorted(self):
        self.app.add_part("RES-Z", "Resistor Z")
        self.app.add_part("CAP-A", "Capacitor A")
        self.assertEqual([r["sku"] for r in self.app.list_catalog("parts")], ["CAP-A", "RES-Z"])
        with self.assertRaises(ValidationError): self.app.list_catalog("other")

    def test_deactivate_empty_bin_and_reactivate(self):
        self.app.add_bin("RACK-A", "Rack")
        self.assertEqual(self.app.set_active("bins", "rack-a", False)["active"], 0)
        self.assertEqual(self.app.list_catalog("bins", active_only=True), [])
        self.assertEqual(self.app.set_active("bins", "rack-a", True)["active"], 1)

    def test_deactivate_empty_part_and_reactivate(self):
        self.app.add_part("RES-10K", "Resistor")
        self.assertEqual(self.app.set_active("parts", "RES-10K", False)["active"], 0)
        self.assertEqual(self.app.set_active("parts", "RES-10K", True)["active"], 1)

    def test_deactivate_with_stock_is_forbidden(self):
        self.bootstrap(); self.stock()
        with self.assertRaises(Conflict): self.app.set_active("parts", "RES-10K", False)
        with self.assertRaises(Conflict): self.app.set_active("bins", "RACK-A", False)
        self.assertTrue(self.app.health()["ok"])

    def test_unchanged_activation_does_not_write_audit(self):
        self.app.add_bin("RACK-A", "Rack")
        before = self.snapshot()
        self.app.set_active("bins", "RACK-A", True)
        self.assertEqual(self.snapshot(), before)

    def test_reorder_point_changes_and_is_audited(self):
        self.bootstrap()
        self.assertEqual(self.app.set_reorder_point("RES-10K", 12)["reorder_point"], 12)
        events = self.snapshot()["catalog_events"]
        self.assertEqual(events[-1][3], "reorder_point_changed")

    def test_reorder_noop_has_no_side_effect(self):
        self.bootstrap()
        before = self.snapshot()
        self.app.set_reorder_point("RES-10K", 5)
        self.assertEqual(self.snapshot(), before)

    def test_unknown_catalog_entry_rejected(self):
        with self.assertRaises(NotFound): self.app.set_reorder_point("UNKNOWN", 1)
        with self.assertRaises(NotFound): self.app.set_active("bins", "MISSING", False)
        with self.assertRaises(ValidationError): self.app.set_active("others", "BIN-1", True)

    def test_reactivate_after_issuing_all_inventory(self):
        self.bootstrap(); self.stock(qty=12)
        self.app.issue("RES-10K", "RACK-A", 12, "ISS-1", purpose="Assembly")
        self.assertEqual(self.app.set_active("parts", "RES-10K", False)["active"], 0)
        with self.assertRaises(Conflict): self.app.receive("RES-10K", "RACK-A", 1, "R-2")
        self.assertEqual(self.app.set_active("parts", "RES-10K", True)["active"], 1)
