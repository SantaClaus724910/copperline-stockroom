# Development

## Run the suite

```bash
python -m unittest discover -s tests -q
python -m unittest tests.test_concurrency -v
python -m scripts.demo
python -m compileall -q copperline scripts tests
```

The test suite uses `unittest` and isolated temporary databases, so a database server and additional packages are not needed. A deterministic clock is used when verifying event timestamps.

## Make a change

Keep inventory calculations in `copperline/service.py`; the CLI should only parse arguments and adapt service responses. Changes that touch current stock should execute in a single write transaction and preserve reference-based replay semantics.

For schema changes, add an explicit migration and test it against a separately preserved database file from the prior version. Never regenerate an old-version test fixture using the current schema. The current implementation refuses schema versions other than 1.

Regression tests should cover the failure path as well as the happy path. For an operation changing two balances, inject a failure after the first write and verify that the entire database is unchanged. For concurrent writes, use independent service instances and ensure unexpected worker exceptions propagate into the test.

## Design constraints

- Keep zero or positive quantities in balances, and represent corrections explicitly in the ledger.
- Normalize SKUs, bin codes, and references before hash and uniqueness checks.
- Preserve read-only integrity reporting; never make a health check silently repair data.
- Keep synthetic sample inputs separate from runtime databases.
- Avoid introducing network services into the local workflow without an explicit need.
