# ADR-001: Fail-closed, versioned unit and permit facts

**Status:** Implemented — migration 008 active; permit reconciliation in progress
**Date:** 2026-07-13
**Deciders:** Yaroslav Korets

## Context

The genesis site and database contained published unit and permit values that
did not all pass through `document → claim → validation → normalization →
review → publish`. The publisher checked aggregate arithmetic but did not
require a semantically compatible receipt for every displayed field.

Promotion also deleted the previous unit row. Because `fact_provenance` has a
polymorphic target rather than a real foreign key, those deletes left orphaned
receipts. A receipt could also point at an unrelated claim field and still
look complete in a simple coverage count.

Southaven exposed a modeling error: a source-supported cohort total of at
least 495 MW was divided by 27 and stored as `mw_each`. The source does not say
the 27 machines are identical or individually rated at 18.333333 MW.

## Decision

1. Publishing and promotion fail closed for every non-null unit and permit
   field. A receipt counts only if its claim is validated, anchored, archived,
   field-compatible, and value-compatible.
2. Generic `permit.date` is retired. Extraction must classify dates as
   `permit.filed_at` or `permit.issued_at` from explicit source language.
3. Unknown OEM, model, and fuel values are `NULL`, not labels or defaults that
   resemble facts.
4. Claim validation and fact verification are separate. `claim.status =
   validated` means the extraction is anchored to the archived source; it does
   not make the source's assertion true. Unit and permit facts receive an
   explicit verification state: `source_asserted`, `corroborated`, `verified`,
   or `disputed`.
5. Unit rows receive a `basis` (`permitted`, `observed`, `reported`) and an
   optional direct `total_mw`. A cohort total is never converted to `mw_each`
   unless the source explicitly supports an individual rating.
6. Changed rows are versioned: prior published rows become `retracted`; they
   are not deleted. Unchanged receipts are copied to the staged version before
   review.
7. Direct and derived provenance are distinct. Derived facts must record a
   formula and all inputs; an arbitrary note is insufficient.
8. Database triggers reject new missing-target, unvalidated, or
   field-incompatible provenance and record every unit, permit, and provenance
   mutation in an append-only audit log.
9. No data reconciliation is applied as part of the schema migration. Each
   current violation becomes an explicit review item sourced from archived
   claims.
10. A review is an immutable manifest of exact staged row IDs, sealed with a
    SHA-256 hash. The merged review file carries the review ID and hash.
11. Promotion is one idempotent database transaction: verify manifest and
    evidence, retract prior logical versions, publish only manifest rows,
    recompute the aggregate, and record the merge commit. Database triggers
    reject equivalent piecemeal REST updates.
12. Exact regulatory phrases may produce controlled permit claims through a
    deterministic parser for archived PDF or HTML records. Only this parser,
    never an ordinary LLM claim, may propose replacement of an already
    populated but truth-gate-unsupported permit field. The replacement still
    creates a staged version and requires the same sealed review and atomic
    promotion as every other correction.
13. Reconciliation may replay the entire validated claim corpus, but an exact
    field/claim receipt already attached to the current published version is a
    no-op. A new staged version requires new evidence, a fact-field change, or
    a basis/verification-state change; repeated runs cannot manufacture
    identical versions from already reviewed receipts.

## Options considered

### A. Continue manual backfills

Low implementation cost, but correctness depends on reviewer discipline and
the same class of mismatch can recur. Rejected.

### B. Add only a publisher coverage count

Stops missing receipts but accepts semantically wrong links and derived values
masquerading as direct facts. Rejected.

### C. Versioned facts plus semantic gates

More migration and reconciliation work, but it makes unsupported publication
mechanically impossible and preserves a trace of every correction. Selected.

## Consequences

- The current database will not pass the new truth gate immediately. This is
  expected and is preferable to silently publishing incomplete provenance.
- Some documents must be re-extracted with typed permit dates and fuel/type
  fields before their facts can return to `published`.
- Southaven must display `27 observed units · ≥495 MW total`; it must not show
  `18.333333 MW each`. Until independently corroborated, the total must be
  labelled as an assertion from the cited court filing, not BTW-verified MW.
- Review creation fails closed when any proposed unit/permit field lacks a
  compatible receipt. Promotion rechecks the same gate inside the transaction.
- Compound permit statuses require one direct receipt per component; evidence
  for `issued` cannot silently cover `under appeal`.
- Migration 008 and the atomic rollback/idempotency scenarios pass against a
  clean local PostgreSQL 17 database. The same clean-database scenario must
  pass in GitHub Actions before applying the migration to production.

## Action items

1. Open a GitHub PR and run migrations 001–008 plus
   `engine/tests/sql/008_truth_integrity_regression.sql` against the workflow's
   isolated PostgreSQL 17 service.
2. Run the read-only truth audit and turn every violation into a staged review
   item; do not patch published rows directly.
3. Re-extract the archived genesis documents with typed permit dates.
4. Reconcile Southaven into `basis=observed`, `total_mw=495`, `mw_each=NULL`,
   `verification_state=source_asserted`, preserving the source's “at least”
   qualifier in the public representation.
5. Validate the new database constraints only after the reconciliation review
   is merged.
