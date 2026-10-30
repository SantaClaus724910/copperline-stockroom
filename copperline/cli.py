"""JSON command line for local stockroom operations."""

from __future__ import annotations

import argparse
import json
import sys

from .errors import StockroomError
from .service import Stockroom


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="copperline", description="Electronics parts stockroom and movement ledger")
    parser.add_argument("--db", default="data/stockroom.sqlite", help="Local SQLite database; place before the command")
    groups = parser.add_subparsers(dest="group", required=True)
    groups.add_parser("init")

    parts = groups.add_parser("part").add_subparsers(dest="action", required=True)
    p = parts.add_parser("add")
    p.add_argument("sku"); p.add_argument("description")
    p.add_argument("--unit", default="pcs"); p.add_argument("--reorder", type=int, default=0)
    parts.add_parser("list")
    p = parts.add_parser("show"); p.add_argument("sku")
    p = parts.add_parser("reorder"); p.add_argument("sku"); p.add_argument("quantity", type=int)
    for action in ("activate", "deactivate"):
        parts.add_parser(action).add_argument("sku")

    bins = groups.add_parser("bin").add_subparsers(dest="action", required=True)
    p = bins.add_parser("add"); p.add_argument("code"); p.add_argument("description")
    bins.add_parser("list")
    for action in ("activate", "deactivate"):
        bins.add_parser(action).add_argument("code")

    stock = groups.add_parser("stock").add_subparsers(dest="action", required=True)
    p = stock.add_parser("receive")
    p.add_argument("sku"); p.add_argument("bin"); p.add_argument("quantity", type=int)
    p.add_argument("--ref", required=True); p.add_argument("--note", default="")
    p = stock.add_parser("move")
    p.add_argument("sku"); p.add_argument("source"); p.add_argument("destination"); p.add_argument("quantity", type=int)
    p.add_argument("--ref", required=True); p.add_argument("--note", default="")
    p = stock.add_parser("issue")
    p.add_argument("sku"); p.add_argument("bin"); p.add_argument("quantity", type=int)
    p.add_argument("--ref", required=True); p.add_argument("--purpose", required=True)
    p = stock.add_parser("count")
    p.add_argument("sku"); p.add_argument("bin"); p.add_argument("quantity", type=int)
    p.add_argument("--ref", required=True); p.add_argument("--reason", required=True)
    p = stock.add_parser("history"); p.add_argument("--sku"); p.add_argument("--limit", type=int, default=100)

    ingest = groups.add_parser("import").add_subparsers(dest="action", required=True)
    p = ingest.add_parser("receipts"); p.add_argument("source"); p.add_argument("--ref", required=True)

    report = groups.add_parser("report").add_subparsers(dest="action", required=True)
    for action in ("inventory", "low-stock", "health"):
        report.add_parser(action)

    export = groups.add_parser("export").add_subparsers(dest="action", required=True)
    p = export.add_parser("inventory"); p.add_argument("--output", required=True)
    return parser


def dispatch(app: Stockroom, args):
    action = args.action if args.group not in {"init"} else None
    if args.group == "init":
        return {"database": str(app.database), **app.schema_status}
    if args.group == "part":
        if action == "add": return app.add_part(args.sku, args.description, unit=args.unit, reorder_point=args.reorder)
        if action == "list": return app.list_catalog("parts")
        if action == "show": return app.stock_by_part(args.sku)
        if action == "reorder": return app.set_reorder_point(args.sku, args.quantity)
        if action in {"activate", "deactivate"}: return app.set_active("parts", args.sku, action == "activate")
    if args.group == "bin":
        if action == "add": return app.add_bin(args.code, args.description)
        if action == "list": return app.list_catalog("bins")
        if action in {"activate", "deactivate"}: return app.set_active("bins", args.code, action == "activate")
    if args.group == "stock":
        if action == "receive": return app.receive(args.sku, args.bin, args.quantity, args.ref, note=args.note)
        if action == "move": return app.move(args.sku, args.source, args.destination, args.quantity, args.ref, note=args.note)
        if action == "issue": return app.issue(args.sku, args.bin, args.quantity, args.ref, purpose=args.purpose)
        if action == "count": return app.correct_count(args.sku, args.bin, args.quantity, args.ref, reason=args.reason)
        if action == "history": return app.history(sku=args.sku, limit=args.limit)
    if args.group == "import" and action == "receipts":
        return app.import_receipts(args.source, args.ref)
    if args.group == "report":
        if action == "inventory": return app.inventory()
        if action == "low-stock": return app.low_stock()
        if action == "health": return app.health()
    if args.group == "export" and action == "inventory":
        return app.export_inventory(args.output)
    raise AssertionError("Unhandled command")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = dispatch(Stockroom(args.db), args)
    except StockroomError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 3 if args.group == "report" and args.action == "health" and not result["ok"] else 0


def entrypoint():
    raise SystemExit(main())
