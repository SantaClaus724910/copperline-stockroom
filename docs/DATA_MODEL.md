# Data model

The stockroom uses a single SQLite database. Catalog rows identify parts and bins. The `balances` table is a current-state projection; `stock_events` is the movement ledger used to check that projection.

## Tables

| Table | Content | Important rule |
| --- | --- | --- |
| `parts` | SKU, description, unit, reorder point, active flag | Unique normalized SKU |
| `bins` | Bin code, description, active flag | Unique normalized bin code |
| `balances` | Part/bin pair and nonnegative quantity | One row per pair |
| `stock_events` | Receipts, moves, issues, count corrections | Unique individual reference, or one row per import batch position |
| `import_batches` | Imported receipt batches and content hashes | Global unique batch reference |
| `catalog_events` | Part/bin activation and reorder threshold changes | Append-only through the service |
| `schema_meta` | Database schema version | Version 1 |

A movement always has one stock event. A receipt adds to `to_bin_id`. An issue subtracts from `from_bin_id`. A move subtracts from the source and adds to the destination in the same database transaction. A count adjustment adds a signed delta to the counted bin; `target_quantity` stores the operator's requested absolute count. Stock events are kept even when a bin's current balance becomes zero.

## References and replay

Each single movement stores a nonempty reference and a SHA-256 hash of its normalized request. A replay checks the reference before revalidating the current catalog or balance, so an accepted command remains recognizable after stock changes. Changed content with the same reference is rejected.

Imported rows have no individual public reference. They use `(batch_id, batch_row)`, unique within an accepted import. The import batch stores a hash of the normalized **ordered** row list. An identical import retry performs no writes, even if the original bin or part has since been deactivated. Reordering rows changes the batch identity, because the row sequence is part of the accepted ledger history.

## Integrity model

`report health` compares each balance with the sum of all ledger credits and debits for its part/bin pair. It also reports missing balance rows, foreign-key issues, SQLite integrity errors, and discrepancies between import batch row counts and their movement events.

The health check is read-only. It detects accidental mismatches but is not cryptographic protection against someone deliberately modifying every related row in the database file.

## Consistency and time

Write transactions use `BEGIN IMMEDIATE` so balance validation and movement writes are serialized. Reads use one snapshot per operation. SQLite has a ten-second busy timeout. If that expires, a storage error surfaces; it is not disguised as an inventory conflict. New transactions use their own connections.

Timestamps use second-precision UTC from an injectable aware clock. Movement order is determined by event IDs rather than timestamp ties. A stock event preserves the original recorded result when replayed.
