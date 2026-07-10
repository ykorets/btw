# Decision log (ADR-lite)

## D1 · Postgres is the single source of truth; git is a generated mirror
Context: v1 had three stores held together by convention → drift risk.
Decision: Supabase Postgres authoritative (FKs, UUID provenance); the public
"ledger" repo is regenerated on publish, never hand-edited. Merge webhook
promotes staging→published. Consistency check gates publish.

## D2 · Files are the canonical API
Static CSV/JSON on CDN, versioned by git tag. REST facade (Worker + D1 over
the same snapshot) only after the first concrete consumer request. Operational
Postgres is never exposed publicly.

## D3 · Pipeline runs on GitHub Actions cron, not Workers
Refines architecture §2: extraction needs PDF/OCR libs and headless browsers —
awkward on Workers, native on Actions. Free minutes cover our volume; the run
is a replayable script either way. Workers keep: hot-lane webhook, future read
facade. Revisit if runs exceed Actions limits.

## D4 · Two repos
`btw` (this, code+site) and `btw-data` (generated mirror; community PRs =
correction proposals ingested by bot). Data repo history = public audit log.

## D5 · Anchoring v2 is mandatory for numeric facts
Claim → anchor (quote|cell|figure) + fuzzy-normalized match ≥ 0.92 + numeric
inventory cross-check. Unverifiable claims cannot reach facts (schema-level).

## D6 · Island Watch is a first-class verification component
For grid-avoiding islands, registries are structurally blind. Primary evidence:
satellite/thermal + interstate gas nominations. "Operating" needs two
independent evidence classes; press releases inadmissible.

## D7 · Wordmark in IBM Plex Mono; brand rule "mono = quotes the record"
The wordmark is the single exception: the brand itself is a quote from the
public record. See site/brand/design-system.html.
