# Copperline Stockroom

**Parts inventory and movement tracking for electronics workbenches and small workshops.**

Copperline Stockroom is a local, Python-based inventory tool for keeping electronic components organized across storage bins. It records stock receipts, moves between bins, materials used during builds, and corrections from physical stock counts. A reference-based movement ledger makes repeated commands safe to retry, and low-stock reports help plan replenishment.

The application is intended for an individual workshop or a small, shared bench where a lightweight command-line tool is easier to maintain than a hosted inventory system. It runs entirely on the local machine with SQLite and the Python standard library. No accounts, hosted services, or network access are required.

## Requirements

- Python 3.11 or newer
- A local directory where you can write the database
- No third-party runtime packages

## Quick start

Run these commands from the repository root:

```bash
python -m unittest discover -s tests -q
python -m scripts.demo
python -m copperline --help
```

The demo runs with a temporary SQLite database, uses the sample components in `examples/receipts.csv`, and prints a JSON summary. A healthy run finishes with `"health_ok": true`.

Initialize a persistent stockroom:

```bash
python -m copperline --db data/stockroom.sqlite init
```

The `--db` option is global and must appear before the command. You can omit it to use `data/stockroom.sqlite`.

## A typical workshop workflow

Start by defining bins and parts:

```bash
python -m copperline --db data/stockroom.sqlite bin add RACK-A1 "Passive components rack"
python -m copperline --db data/stockroom.sqlite bin add DRAWER-B2 "Assembly bench drawer"

python -m copperline --db data/stockroom.sqlite part add RES-10K-0603 "10 kOhm 0603 resistor" --reorder 150
python -m copperline --db data/stockroom.sqlite part add CAP-100NF-0603 "100 nF ceramic capacitor" --reorder 400
python -m copperline --db data/stockroom.sqlite part add LED-GRN-5MM "Green 5 mm LED" --reorder 150
```

Receive a delivery of sample stock, then move a portion to the assembly bench:

```bash
python -m copperline --db data/stockroom.sqlite import receipts examples/receipts.csv --ref SUPPLY-001
python -m copperline --db data/stockroom.sqlite stock move RES-10K-0603 RACK-A1 DRAWER-B2 100 --ref MOVE-001
```

Record parts consumed during a build and correct a counted bin:

```bash
python -m copperline --db data/stockroom.sqlite stock issue RES-10K-0603 DRAWER-B2 40 --ref BUILD-001 --purpose "Controller board assembly"
python -m copperline --db data/stockroom.sqlite stock count LED-GRN-5MM DRAWER-B2 118 --ref COUNT-001 --reason "Shelf recount"
```

Inspect the stockroom and export a spreadsheet-compatible CSV:

```bash
python -m copperline --db data/stockroom.sqlite part show RES-10K-0603
python -m copperline --db data/stockroom.sqlite stock history --sku RES-10K-0603
python -m copperline --db data/stockroom.sqlite report low-stock
python -m copperline --db data/stockroom.sqlite report inventory
python -m copperline --db data/stockroom.sqlite report health
python -m copperline --db data/stockroom.sqlite export inventory --output exports/stock.csv
```

## Capabilities

| Area | Behavior |
| --- | --- |
| Part catalog | Normalized SKUs, descriptions, units, reorder thresholds, active/inactive state |
| Storage bins | Named locations with safeguards against deactivating stocked bins |
| Receipts | Inbound quantity recorded in the balance and event ledger |
| Bin movements | One atomic operation debits one bin and credits another |
| Material usage | Issue parts for a named build purpose without storing personal details |
| Physical counts | Adjust a bin to a counted total, recording the signed correction and reason |
| CSV intake | Multi-row receipt imports with whole-batch rollback and content-checked retries |
| Retry handling | Accepted movement references are replayed without changing stock again |
| Reporting | On-hand by part/bin, low-stock thresholds, chronological movement history |
| CSV export | Temporary-file write followed by atomic replacement; spreadsheet-safe text escaping |
| Integrity checks | SQLite and foreign-key validation, ledger/balance reconciliation, import-count checks |

## Important rules

Stock quantities are nonnegative integers. A move or issue cannot consume more units than the source bin contains, and quantity overflow is rejected. A failed multi-step operation rolls back all of its changes.

Every individual movement has a reference, such as `MOVE-001`. Repeating a reference with the same normalized details returns the original event with `"replayed": true` and makes no new entries. Reusing that reference with changed details is a conflict. CSV imports use their own batch references and preserve row order as part of the batch identity.

A physical count is an absolute target, not an amount to add or remove. For example, counting 42 units when the balance shows 45 records a correction of -3. Counts that match the stored balance do not generate an event.

The ledger is append-only through the application interface. Physical access to the SQLite file still permits external modifications; `report health` checks for inconsistencies but does not repair them.

## Project structure

```text
copperline/
  cli.py          CLI arguments, JSON responses, exit codes
  service.py      Stock movements, catalog, imports, and reports
  db.py           SQLite schema, initialization, transactions
  validation.py   Input validation and normalization
  errors.py       Domain exception types
examples/
  receipts.csv    Synthetic electronics component deliveries
scripts/
  demo.py         Reproducible temporary-database workflow
tests/            Standard-library unittest regression suite
docs/             Data model, operator notes, and development guide
ROADMAP.md        Planned feature work and completion criteria
```

## Testing

```bash
python -m unittest discover -s tests -q
python -m unittest tests.test_concurrency -v
python -m scripts.demo
python -m compileall -q copperline scripts tests
```

The tests use isolated temporary databases. No service credentials, API keys, or customer records are needed. `report health` returns JSON; when invoked through the CLI, it exits with status 3 if a mismatch is found. Other expected input and inventory conflicts exit with status 2. Unexpected storage or programming faults are not hidden as business errors.

## Current boundaries

This version covers a single local stockroom database. It does not yet manage bills of materials, component reservations, shared multi-user access, cycle-count sessions, reorder purchase orders, barcode readers, or synchronization across computers. Those features and their acceptance criteria are described in [ROADMAP.md](ROADMAP.md).

Only component names, synthetic stock references, quantities, and bin identifiers are required. Do not put real names, addresses, credentials, or confidential order records into example files. Keep runtime databases and exports outside version control; `.gitignore` excludes the default paths.

## License

Distributed under the [Unlicense](LICENSE).
