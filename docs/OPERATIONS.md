# Operator notes

## Set up a stockroom

Run `python -m copperline --db data/stockroom.sqlite init` to create the database. Existing version-1 files are checked before use and are never reset on open. Unsupported schema versions are refused, not downgraded.

Use catalog codes that are stable and meaningful at the workbench, such as `RES-10K-0603` and `RACK-A1`. Codes are normalized to uppercase and limited to letters, digits, dots, underscores, and hyphens.

Parts and bins with remaining stock cannot be deactivated. To retire a bin, move or issue its stock first. To retire a part, bring all of its balances to zero before deactivating it. This avoids leaving inventory inaccessible to ordinary movement commands.

## Record work

Always supply a unique reference when receiving, moving, issuing, or counting components. A reference can be retried safely after an interrupted terminal session, but it cannot be reused for different work.

`stock count` takes the **actual number of units observed in that bin**. It does not take a difference. For a count adjustment, provide a short reason such as "Shelf recount". A count that matches the stored total is reported as a no-op conflict; no ledger event is added.

The parts catalog can track a reorder threshold per part. `report low-stock` compares that threshold against the total across all bins; zero-threshold and inactive parts are omitted.

## Batch receipt imports

Use `import receipts FILE.csv --ref BATCH-REFERENCE` for multi-line stock delivery files. Headers must contain exactly `sku`, `bin_code`, `quantity`, and `note` (in any order). Files are UTF-8 with an optional BOM, at most 5 MB and 5,000 receipt rows. The entire file either commits or rolls back; errors include invalid part and bin codes, unknown or inactive entries, bad quantities, and malformed CSV.

For corrections to accepted stock, use new movement or count references; do not reuse an import reference with changed rows.

## Export files

`export inventory --output exports/stock.csv` writes a temporary file in the destination directory and atomically replaces the final CSV. If serialization fails, the previous file is preserved. Values that could be interpreted as spreadsheet formulas are escaped in output without changing stored values.

## Backup and sharing

A local stockroom database is a SQLite file. Close active writers before copying it, or use the SQLite online backup API for a live file. Verify a restored copy using `report health` before relying on it. Do not include real inventory databases, secrets, or confidential records in public examples or commits.

The tool does not have account authentication or multi-user permissions. Keep the database in a directory protected by the operating system. A hosted deployment or shared network filesystem needs a separate design review.
