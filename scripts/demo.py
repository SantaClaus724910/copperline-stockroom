"""Repeatable inventory workflow backed by a disposable SQLite file."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from copperline import Stockroom


def run_demo():
    with tempfile.TemporaryDirectory(prefix="copperline-demo-") as tmp:
        root = Path(tmp)
        app = Stockroom(root / "store.sqlite", now=lambda: datetime(2026, 10, 30, 9, tzinfo=timezone.utc))
        app.add_bin("RACK-A1", "Main passive components")
        app.add_bin("DRAWER-B2", "Assembly bench")
        app.add_part("RES-10K-0603", "10 kOhm 0603 resistor", reorder_point=150)
        app.add_part("CAP-100NF-0603", "100 nF ceramic capacitor", reorder_point=400)
        app.add_part("LED-GRN-5MM", "Green 5 mm LED", reorder_point=150)
        source = Path(__file__).resolve().parents[1] / "examples" / "receipts.csv"
        imported = app.import_receipts(source, "SUPPLY-001")
        replay = app.import_receipts(source, "SUPPLY-001")
        app.move("RES-10K-0603", "RACK-A1", "DRAWER-B2", 100, "MOVE-001")
        used = app.issue("RES-10K-0603", "DRAWER-B2", 40, "BUILD-001", purpose="Controller board assembly")
        app.correct_count("LED-GRN-5MM", "DRAWER-B2", 118, "COUNT-001", reason="Shelf recount")
        result = {
            "imported_rows": imported["row_count"], "import_replayed": replay["replayed"],
            "issued_event": used["kind"],
            "resistors_on_hand": app.stock_by_part("RES-10K-0603")["on_hand"],
            "low_stock_skus": [p["sku"] for p in app.low_stock()],
            "movement_count": len(app.history()), "health_ok": app.health()["ok"],
        }
        app.export_inventory(root / "out" / "stock.csv")
        return result


if __name__ == "__main__":
    print(json.dumps(run_demo(), indent=2))
