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

## D8 · Extraction is model-agnostic; default picked by evals, not brand
Anchoring v2 (quote match + numeric cross-check) makes cheap models safe:
hallucinations are rejected mechanically regardless of who generated them.
Extractor interface takes any LLM; `extractor_version` stamps every claim.
At M3, run the eval set across Claude Haiku/Sonnet and Gemini Flash; the
precision/cost table picks the default. Strong model reserved for docs the
cheap one fails; dual-model OCR pass uses a *different* family on purpose.

## D9 · LiteLLM as transport, roles over brands, quorum extraction
No provider is hardcoded. LiteLLM SDK gives one interface + open price table
(model_prices_and_context_window.json) for a per-call cost ledger. models.yaml
maps roles (default_extractor, cross_checker, escalation, digest_writer) to
models; evals pick assignments. Quorum policy: two cheap extractors from
different families agreeing (after anchor validation) beats one premium model
on both cost and confidence; disagreement escalates. OpenRouter considered and
deferred: ~5% fee + a dependency in the critical path; revisit if key
management becomes a burden.

