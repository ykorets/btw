# btw engine

Pipeline: watch → fetch/archive → extract (anchored claims) → normalize/resolve
→ review (PR gate) → publish (files-as-API). See ../docs/architecture.md.

Runtime: Python 3.11+, scheduled by GitHub Actions (docs/decisions.md · D3).
Storage: Supabase Postgres (schema/) · Cloudflare R2 (archive) · btw-data (mirror).

Modules map 1:1 to architecture components:
watch.py · fetch.py · extract.py · normalize.py · review.py · publish.py · island_watch.py

## Local development

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked --extra dev
uv run pytest tests -q
```

`uv.lock` is the reproducible dependency snapshot used locally and in CI.

## Deterministic regulatory claims

Archived permit PDFs and registry HTML can be processed without an LLM:

```bash
uv run python -m btw_engine.extract --sha <sha256> --deterministic-only
```

The parser emits only exact, quote-anchored regulatory classifications. A
unique deterministic claim may repair a populated permit field only when the
existing value already fails migration 008's semantic gate. The repair is a
staging version, not an in-place update, and still requires a sealed review.
Registry rows must also emit their explicit permit number so every claim is
scoped by a source key rather than by project name or geography.

Reconciliation is replay-safe across the full validated-claim corpus. An
exact `(fact version, field, claim)` receipt that has already been reviewed is
a no-op; only a new receipt, field value, basis, or verification state can
create another staged version.

## Truth audit

Run the read-only unit/permit provenance audit before any publish or data
reconciliation:

```bash
uv run python -m btw_engine.audit
```

It exits non-zero if a displayed field lacks a validated, archived,
field-compatible claim. `publish.py`, review-manifest creation, and atomic
promotion run the same fail-closed rule. The merged review file approves exact
row IDs sealed by SHA-256; promotion never scans "whatever is staging".

Schema hardening is documented in
`../docs/ADR-001-verifiable-fact-versions.md`. Before production, migration 008
must pass in GitHub Actions against its isolated PostgreSQL 17 service. The
database regression scenario is `tests/sql/008_truth_integrity_regression.sql`.
