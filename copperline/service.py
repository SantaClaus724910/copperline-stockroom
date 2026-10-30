"""Parts catalog, stock movement ledger, and operational reports."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .db import initialize, transaction
from .errors import Conflict, NotFound, ValidationError
from .validation import MAX_QTY, code, label, optional_note, quantity, utc_stamp

MAX_IMPORT_BYTES = 5_000_000
MAX_IMPORT_ROWS = 5_000
IMPORT_HEADERS = ("sku", "bin_code", "quantity", "note")


def _digest(data: object) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


class Stockroom:
    """Local store with a separate SQLite connection per operation."""

    def __init__(self, database: str | Path, *, now=None):
        self.database = Path(database)
        self._clock = now or (lambda: datetime.now(timezone.utc))
        self.schema_status = initialize(self.database)

    def _stamp(self) -> str:
        return utc_stamp(self._clock)

    @staticmethod
    def _resolve(con, table: str, value: str, *, active: bool = False):
        if table not in {"parts", "bins"}:
            raise AssertionError("Invalid catalog")
        field = "sku" if table == "parts" else "code"
        row = con.execute(f"SELECT * FROM {table} WHERE {field}=?", (value,)).fetchone()
        if row is None:
            raise NotFound(f"Unknown {table[:-1]} {value}")
        if active and not row["active"]:
            raise Conflict(f"{table[:-1].capitalize()} {value} is inactive")
        return row

    @staticmethod
    def _audit(con, entity: str, ident: int, action: str, details: dict, at: str):
        con.execute("INSERT INTO catalog_events(entity_type,entity_id,action,details,created_utc) VALUES (?,?,?,?,?)",
                    (entity, ident, action, json.dumps(details, sort_keys=True), at))

    @staticmethod
    def _integrity_as_conflict(exc: sqlite3.IntegrityError, what: str):
        if "UNIQUE constraint failed" in str(exc):
            return Conflict(f"{what} already exists")
        return exc

    def add_part(self, sku: str, description: str, *, unit: str = "pcs", reorder_point: int = 0) -> dict:
        sku = code(sku, "SKU")
        description = label(description, "Description")
        unit = label(unit, "Unit", max_length=16)
        reorder_point = quantity(reorder_point, "Reorder point", minimum=0)
        with transaction(self.database, write=True) as con:
            at = self._stamp()
            try:
                cur = con.execute("INSERT INTO parts(sku,description,unit,reorder_point,created_utc) VALUES (?,?,?,?,?)",
                                  (sku, description, unit, reorder_point, at))
            except sqlite3.IntegrityError as exc:
                raise self._integrity_as_conflict(exc, "SKU") from exc
            self._audit(con, "part", cur.lastrowid, "created", {"sku": sku, "reorder_point": reorder_point}, at)
            return dict(con.execute("SELECT * FROM parts WHERE id=?", (cur.lastrowid,)).fetchone())

    def add_bin(self, bin_code: str, description: str) -> dict:
        bin_code, description = code(bin_code, "Bin code"), label(description, "Description")
        with transaction(self.database, write=True) as con:
            at = self._stamp()
            try:
                cur = con.execute("INSERT INTO bins(code,description,created_utc) VALUES (?,?,?)", (bin_code, description, at))
            except sqlite3.IntegrityError as exc:
                raise self._integrity_as_conflict(exc, "Bin code") from exc
            self._audit(con, "bin", cur.lastrowid, "created", {"code": bin_code}, at)
            return dict(con.execute("SELECT * FROM bins WHERE id=?", (cur.lastrowid,)).fetchone())

    def list_catalog(self, table: str, *, active_only: bool = False) -> list[dict]:
        if table not in {"parts", "bins"}:
            raise ValidationError("Catalog must be parts or bins")
        with transaction(self.database) as con:
            field = "sku" if table == "parts" else "code"
            return [dict(r) for r in con.execute(f"SELECT * FROM {table}" + (" WHERE active=1" if active_only else "") + f" ORDER BY {field}")]

    def set_active(self, table: str, name: str, active: bool) -> dict:
        if table not in {"parts", "bins"} or not isinstance(active, bool):
            raise ValidationError("Specify a parts/bins catalog and true/false activation")
        name = code(name, "SKU" if table == "parts" else "Bin code")
        with transaction(self.database, write=True) as con:
            row = self._resolve(con, table, name)
            if row["active"] == int(active):
                return dict(row)
            if not active:
                col = "part_id" if table == "parts" else "bin_id"
                if con.execute(f"SELECT 1 FROM balances WHERE {col}=? AND quantity>0 LIMIT 1", (row["id"],)).fetchone():
                    raise Conflict("Move or issue remaining stock before deactivating")
            at = self._stamp()
            con.execute(f"UPDATE {table} SET active=? WHERE id=?", (int(active), row["id"]))
            self._audit(con, "part" if table == "parts" else "bin", row["id"], "activated" if active else "deactivated", {}, at)
            return dict(self._resolve(con, table, name))

    def set_reorder_point(self, sku: str, threshold: int) -> dict:
        sku = code(sku, "SKU")
        threshold = quantity(threshold, "Reorder point", minimum=0)
        with transaction(self.database, write=True) as con:
            part = self._resolve(con, "parts", sku)
            if part["reorder_point"] == threshold:
                return dict(part)
            at = self._stamp()
            con.execute("UPDATE parts SET reorder_point=? WHERE id=?", (threshold, part["id"]))
            self._audit(con, "part", part["id"], "reorder_point_changed", {"from": part["reorder_point"], "to": threshold}, at)
            return dict(self._resolve(con, "parts", sku))

    @staticmethod
    def _current(con, part_id: int, bin_id: int) -> int:
        row = con.execute("SELECT quantity FROM balances WHERE part_id=? AND bin_id=?", (part_id, bin_id)).fetchone()
        return row[0] if row else 0

    @classmethod
    def _delta(cls, con, part_id: int, bin_id: int, change: int):
        previous = cls._current(con, part_id, bin_id)
        if (change < 0 and previous < -change) or (change > 0 and previous > MAX_QTY - change):
            raise Conflict("Insufficient stock or quantity limit exceeded")
        updated = previous + change
        con.execute("INSERT INTO balances(part_id,bin_id,quantity) VALUES (?,?,?) "
                    "ON CONFLICT(part_id,bin_id) DO UPDATE SET quantity=excluded.quantity",
                    (part_id, bin_id, updated))
        return updated

    def _write_event(self, con, request: dict, payload_hash: str, *, reference: str | None = None,
                     batch_id: int | None = None, batch_row: int | None = None) -> dict:
        """Apply one normalized movement within the caller's write transaction."""
        kind, sku, from_code, to_code = (request[key] for key in ("kind", "sku", "from_bin", "to_bin"))
        part = self._resolve(con, "parts", sku, active=True)
        src = self._resolve(con, "bins", from_code, active=True) if from_code is not None else None
        dst = self._resolve(con, "bins", to_code, active=True) if to_code is not None else None
        if kind == "move" and src["id"] == dst["id"]:
            raise ValidationError("Source and destination bins must differ")
        amount = request["quantity"]
        target = request["target_quantity"]
        if kind == "count_adjustment":
            previous = self._current(con, part["id"], dst["id"])
            delta = target - previous
            if delta == 0:
                raise Conflict("Count matches recorded quantity; no correction needed")
            self._delta(con, part["id"], dst["id"], delta)
            event_amount = delta
        else:
            if src is not None:
                self._delta(con, part["id"], src["id"], -amount)
            if dst is not None:
                self._delta(con, part["id"], dst["id"], amount)
            event_amount = amount
        at = self._stamp()
        cur = con.execute("""INSERT INTO stock_events
            (kind,part_id,from_bin_id,to_bin_id,quantity,target_quantity,reference,batch_id,batch_row,note,payload_hash,created_utc)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (kind, part["id"], src["id"] if src else None, dst["id"] if dst else None,
             event_amount, target, reference, batch_id, batch_row, request["note"], payload_hash, at))
        return {**dict(con.execute("SELECT * FROM stock_events WHERE id=?", (cur.lastrowid,)).fetchone()), "replayed": False}

    def _operation(self, *, kind: str, sku: str, from_bin: str | None = None, to_bin: str | None = None,
                   qty: int | None = None, target: int | None = None, reference: str, note: str = "") -> dict:
        reference = label(reference, "Reference", max_length=100)
        note = optional_note(note)
        request = {
            "kind": kind, "sku": code(sku, "SKU"),
            "from_bin": code(from_bin, "Source bin") if from_bin is not None else None,
            "to_bin": code(to_bin, "Destination bin") if to_bin is not None else None,
            "quantity": quantity(qty) if qty is not None else None,
            "target_quantity": quantity(target, "Count", minimum=0) if target is not None else None,
            "note": note,
        }
        digest = _digest(request)
        with transaction(self.database, write=True) as con:
            prior = con.execute("SELECT * FROM stock_events WHERE reference=?", (reference,)).fetchone()
            if prior is not None:
                if prior["payload_hash"] != digest:
                    raise Conflict("Reference was already used for a different movement")
                return {**dict(prior), "replayed": True}
            return self._write_event(con, request, digest, reference=reference)

    def receive(self, sku: str, bin_code: str, qty: int, reference: str, *, note: str = "") -> dict:
        return self._operation(kind="receipt", sku=sku, to_bin=bin_code, qty=qty, reference=reference, note=note)

    def move(self, sku: str, from_bin: str, to_bin: str, qty: int, reference: str, *, note: str = "") -> dict:
        return self._operation(kind="move", sku=sku, from_bin=from_bin, to_bin=to_bin, qty=qty, reference=reference, note=note)

    def issue(self, sku: str, from_bin: str, qty: int, reference: str, *, purpose: str) -> dict:
        return self._operation(kind="issue", sku=sku, from_bin=from_bin, qty=qty, reference=reference,
                               note=label(purpose, "Purpose", max_length=160))

    def correct_count(self, sku: str, bin_code: str, target: int, reference: str, *, reason: str) -> dict:
        return self._operation(kind="count_adjustment", sku=sku, to_bin=bin_code, target=target,
                               reference=reference, note=label(reason, "Count reason", max_length=160))

    @staticmethod
    def _parse_receipt_csv(path: str | Path) -> list[dict]:
        with Path(path).open("rb") as handle:
            raw = handle.read(MAX_IMPORT_BYTES + 1)
        if len(raw) > MAX_IMPORT_BYTES:
            raise ValidationError("Import exceeds 5 MB")
        try:
            data = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValidationError("Import must be UTF-8") from exc
        try:
            reader = csv.DictReader(io.StringIO(data, newline=""), strict=True)
            if reader.fieldnames is None or len(reader.fieldnames) != len(IMPORT_HEADERS) or set(reader.fieldnames) != set(IMPORT_HEADERS):
                raise ValidationError("CSV requires sku,bin_code,quantity,note headers")
            rows = []
            for number, row in enumerate(reader, 2):
                if len(rows) >= MAX_IMPORT_ROWS:
                    raise ValidationError("Import exceeds 5000 rows")
                if None in row or any(v is None for v in row.values()):
                    raise ValidationError(f"Line {number} has the wrong number of columns")
                raw_qty = row["quantity"].strip()
                if not raw_qty.isascii() or not raw_qty.isdecimal() or len(raw_qty) > 19:
                    raise ValidationError(f"Line {number} has an invalid quantity")
                request = {"kind": "receipt", "sku": code(row["sku"], "SKU"), "from_bin": None,
                           "to_bin": code(row["bin_code"], "Bin code"), "quantity": quantity(int(raw_qty)),
                           "target_quantity": None, "note": optional_note(row["note"])}
                rows.append(request)
        except csv.Error as exc:
            raise ValidationError(f"Malformed CSV: {exc}") from exc
        if not rows:
            raise ValidationError("Import contains no receipts")
        return rows

    def import_receipts(self, source: str | Path, reference: str) -> dict:
        reference = label(reference, "Import reference", max_length=100)
        rows = self._parse_receipt_csv(source)
        digest = _digest(rows)
        with transaction(self.database, write=True) as con:
            previous = con.execute("SELECT * FROM import_batches WHERE reference=?", (reference,)).fetchone()
            if previous is not None:
                if previous["payload_hash"] != digest:
                    raise Conflict("Import reference was already used with different rows")
                return {**dict(previous), "replayed": True}
            at = self._stamp()
            cur = con.execute("INSERT INTO import_batches(reference,payload_hash,row_count,created_utc) VALUES (?,?,?,?)",
                              (reference, digest, len(rows), at))
            batch_id = cur.lastrowid
            for pos, row in enumerate(rows, 1):
                self._write_event(con, row, _digest(row), batch_id=batch_id, batch_row=pos)
            return {**dict(con.execute("SELECT * FROM import_batches WHERE id=?", (batch_id,)).fetchone()), "replayed": False}

    def stock_by_part(self, sku: str) -> dict:
        sku = code(sku, "SKU")
        with transaction(self.database) as con:
            part = dict(self._resolve(con, "parts", sku))
            locations = [dict(r) for r in con.execute("""SELECT b.code AS bin_code,b.description,bl.quantity
                FROM balances bl JOIN bins b ON b.id=bl.bin_id WHERE bl.part_id=? AND bl.quantity>0 ORDER BY b.code""", (part["id"],))]
            return {**part, "on_hand": sum(loc["quantity"] for loc in locations), "locations": locations}

    def history(self, *, sku: str | None = None, limit: int = 100) -> list[dict]:
        limit = quantity(limit, "History limit")
        if limit > 1000:
            raise ValidationError("History limit is 1000")
        sku = code(sku, "SKU") if sku is not None else None
        with transaction(self.database) as con:
            params: list = []
            condition = ""
            if sku is not None:
                part = self._resolve(con, "parts", sku)
                condition = "WHERE e.part_id=?"
                params.append(part["id"])
            params.append(limit)
            return [dict(r) for r in con.execute(f"""SELECT e.*,p.sku,src.code AS from_bin_code,dst.code AS to_bin_code
                FROM stock_events e JOIN parts p ON p.id=e.part_id
                LEFT JOIN bins src ON src.id=e.from_bin_id LEFT JOIN bins dst ON dst.id=e.to_bin_id
                {condition} ORDER BY e.id DESC LIMIT ?""", params)]

    def inventory(self) -> list[dict]:
        with transaction(self.database) as con:
            return [dict(r) for r in con.execute("""SELECT p.sku,p.description,p.unit,p.reorder_point,p.active,
                b.code AS bin_code,b.description AS bin_description,bl.quantity
                FROM balances bl JOIN parts p ON p.id=bl.part_id JOIN bins b ON b.id=bl.bin_id
                WHERE bl.quantity>0 ORDER BY p.sku,b.code""")]

    def low_stock(self) -> list[dict]:
        with transaction(self.database) as con:
            return [dict(r) for r in con.execute("""SELECT p.sku,p.description,p.unit,p.reorder_point,
                COALESCE(SUM(b.quantity),0) AS on_hand
                FROM parts p LEFT JOIN balances b ON b.part_id=p.id
                WHERE p.active=1 AND p.reorder_point>0 GROUP BY p.id
                HAVING COALESCE(SUM(b.quantity),0)<=p.reorder_point ORDER BY p.sku""")]

    def health(self) -> dict:
        with transaction(self.database) as con:
            integrity = [r[0] for r in con.execute("PRAGMA integrity_check")]
            foreign_keys = [list(r) for r in con.execute("PRAGMA foreign_key_check")]
            ledger_diff = [dict(r) for r in con.execute("""WITH pairs AS (
                SELECT part_id,from_bin_id AS bin_id FROM stock_events WHERE from_bin_id IS NOT NULL
                UNION SELECT part_id,to_bin_id AS bin_id FROM stock_events WHERE to_bin_id IS NOT NULL
                UNION SELECT part_id,bin_id FROM balances
                ), reconstructed AS (
                SELECT pairs.part_id,pairs.bin_id,
                  COALESCE((SELECT SUM(CASE WHEN e.to_bin_id=pairs.bin_id THEN e.quantity ELSE 0 END
                      - CASE WHEN e.from_bin_id=pairs.bin_id THEN e.quantity ELSE 0 END)
                      FROM stock_events e WHERE e.part_id=pairs.part_id AND
                      (e.to_bin_id=pairs.bin_id OR e.from_bin_id=pairs.bin_id)),0) AS ledger_quantity,
                  bl.quantity AS stored_quantity
                FROM pairs LEFT JOIN balances bl ON bl.part_id=pairs.part_id AND bl.bin_id=pairs.bin_id)
                SELECT * FROM reconstructed WHERE stored_quantity IS NULL OR ledger_quantity!=stored_quantity
                ORDER BY part_id,bin_id""")]
            batch_diff = [dict(r) for r in con.execute("""SELECT b.id,b.reference,b.row_count,COUNT(e.id) AS actual_rows
                FROM import_batches b LEFT JOIN stock_events e ON e.batch_id=b.id GROUP BY b.id
                HAVING b.row_count!=COUNT(e.id) ORDER BY b.id""")]
            return {"ok": integrity == ["ok"] and not foreign_keys and not ledger_diff and not batch_diff,
                    "sqlite_integrity": integrity, "foreign_key_violations": foreign_keys,
                    "balance_disagreements": ledger_diff, "import_disagreements": batch_diff}

    @staticmethod
    def _csv_safe(value):
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
            return "'" + value
        return value

    def export_inventory(self, destination: str | Path) -> dict:
        rows = self.inventory()
        fields = ("sku", "description", "unit", "reorder_point", "active", "bin_code", "bin_description", "quantity")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=destination.parent,
                                             prefix=".copperline-", delete=False) as handle:
                temporary = Path(handle.name)
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for row in rows:
                    writer.writerow({col: self._csv_safe(row[col]) for col in fields})
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return {"path": str(destination), "rows": len(rows), "columns": list(fields)}
