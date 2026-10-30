# Roadmap

Copperline currently tracks individual parts and their movement between bins. The next development steps focus on the problems that appear once a workshop starts assembling repeatable kits and needs accurate stock availability before work begins.

All items below are **planned work**, not existing capabilities. Time ranges are rough implementation estimates including tests and documentation; they are not time already spent.

## 1. Bill of materials and build kits (8–12 hours)

A kit should describe the parts and quantities needed to assemble a board or fixture. The first milestone is CRUD support for kit definitions and a read-only availability calculation across bins. The next milestone is a transaction that consumes the exact required components for a recorded build.

Acceptance checks: reject missing SKUs and quantities of zero, keep kit revisions visible, prevent partial consumption when one component is short, and verify that a build retry cannot duplicate stock events.

## 2. Component reservations (7–10 hours)

Planned work should be able to reserve stock without removing it from on-hand counts. Reservations need an owner-free work reference, lifecycle statuses, expiry or explicit cancellation, and available-to-promise quantities.

Acceptance checks: two competing reservations must not overbook the same part, canceled reservations release stock, and consumption must associate movement events with the reservation without double-counting.

## 3. Guided cycle count sessions (6–9 hours)

A single `stock count` command works for an ad-hoc correction, but larger stockrooms need multi-bin count sessions. Introduce a session that lists bins and parts, tracks counted/unreviewed lines, previews variances, and posts an entire approved count atomically.

Acceptance checks: preview makes no writes, abandoned sessions do not alter stock, and applying a count against stale inventory reports a conflict instead of silently overwriting intervening movements.

## 4. Replenishment planning (5–7 hours)

Build an operator report that proposes replenishment quantities from reorder thresholds, kit requirements, and optional lead-time estimates. The report should not create external orders or contain supplier/customer personal data.

Acceptance checks: quantities are deterministic, multiple bins aggregate correctly, inactive parts are excluded, and the report remains useful when there is no movement history.

## 5. Versioned schema changes and backup validation (5–7 hours)

Create a version-2 migration for the new planning tables once the data model is agreed. Add a backup-and-restore command using SQLite's backup API and test upgrades using a frozen version-1 fixture.

Acceptance checks: existing stock events remain unchanged, failed migrations roll back, repeat opens are safe, concurrent openers serialize, and backup restoration passes ledger reconciliation.

## 6. Packaging and operator ergonomics (3–5 hours)

Improve reporting for larger inventories, add pagination and configurable defaults, and establish an automated CI test matrix for supported Python versions.

Acceptance checks: commands remain scriptable, errors continue to have stable exit statuses, and CI reproduces the local test suite without a hosted database.

## Development order

Start with the kit data model, then reservations; both affect how an available-stock balance should be interpreted. Cycle counts should follow once reservation invariants are stable. Replenishment and migration work can then build on the finalized schema. Keep each change narrow, include a failing regression case before fixing confirmed bugs, and update the command examples when behavior changes.
