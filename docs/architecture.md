# Behind the Watt — data engine architecture v2.0

Status: accepted · Jul 2026 · owner: Yaroslav
Supersedes v1 (see changelog, §9 — every change answers a named weakness).

## 0. Design premise

Turn scattered government filings into a small, verifiable, open dataset —
daily, with one human in the loop, at near-zero cost.

Three facts shape every decision:

1. **The data is tiny.** Dozens of facilities, thousands of documents.
   Megabytes. Anything that smells of big data is over-engineering.
2. **Provenance IS the product.** Every published number traces to a quote on a
   page of a government document — enforced by schema, not by discipline.
3. **One operator.** Solo founder + AI agents. Boring, inspectable, replayable.

## 1. Requirements

Functional: F1 watch registries daily (launch: 6 adapters + safety nets) ·
F2 archive every source document immutably · F3 extract quote-anchored claims
via LLM · F4 normalize + resolve entities · F5 human review gate with a
designed path to graduated auto-merge · F6 verify operation with island-aware
evidence (satellite, gas flows — not just registries) · F7 publish CSV/JSON
files as the canonical API, site data, events feed, weekly digest draft ·
F8 fast lane for breaking events.

Non-functional: daily batch cadence with a manual hot lane · < $50/month infra,
< $10/month LLM · full replayability from the archive · open-sourceable ·
watcher failures are loud, never silent · **single authoritative store with
enforced referential integrity.**

Constraints: solo maintainer; existing stack only (Cloudflare Workers/R2/D1/
Pages, Supabase, GitHub, Claude API).

## 2. High-level design

```
      ┌──────────────────────────── daily cron ────────────────────────────┐
      │                                                                    │
┌───────────┐  ┌───────────┐  ┌────────────┐  ┌─────────────┐  ┌─────────────────┐
│ WATCHERS  │─▶│  FETCHER  │─▶│ EXTRACTOR  │─▶│ NORMALIZER  │─▶│ REVIEW GATE     │
│ adapters  │  │ + ARCHIVE │  │ LLM→claims │  │ + RESOLVER  │  │ staging→PR→     │
│ w/ SLOs   │  │  (R2)     │  │ anchored   │  │             │  │ merge=promote   │
└───────────┘  └───────────┘  └────────────┘  └──────┬──────┘  └────────┬────────┘
      │                                              │                  │ webhook
┌───────────┐  ┌────────────────────────────────────┐│         ┌────────▼────────┐
│ SAFETY    │  │ ISLAND WATCH (verification)        ││         │ POSTGRES        │
│ NETS      │  │ satellite/thermal · gas nominations││         │ (Supabase)      │
│ EIA-860M  │  │ inspections · litigation · press   │┼────────▶│ SINGLE SOURCE   │
│ news/GDELT│  └────────────────────────────────────┘│         │ OF TRUTH        │
│ FERC/EPA  │                                        │         └────────┬────────┘
└───────────┘                                        │                  │ export (generated)
                                                     │         ┌────────▼────────┐
                                                     │         │ LEDGER MIRROR   │
                                                     │         │ (git, read-only │
                                                     │         │  public audit)  │
                                                     │         └────────┬────────┘
                                                     │                  │ build
                                                     │         ┌────────▼────────┐
                                                     │         │ PUBLISHER       │
                                                     │         │ files = the API │
                                                     │         │ site · digest   │
                                                     │         └─────────────────┘
```

### The one-truth rule (fixes v1's three-store drift)

**Postgres is the single authoritative store.** Facts, claims, documents
metadata, provenance — one schema, real foreign keys, transactions. The git
"Ledger" of v1 is demoted to a **generated, read-only mirror**: regenerated
from Postgres on every publish, never hand-edited. It keeps everything git gave
us — public audit history, diffs, citability, forkability — while integrity is
enforced by the database, not by convention.

- Review flow: pipeline writes proposed facts to Postgres in `staging` status →
  bot opens a PR against the mirror showing the human-readable diff (quotes
  inline) → **merge webhook promotes staging→published in Postgres** → export
  regenerates the mirror → Pages build publishes files. Merge is still the
  publish button; the DB is still the only writer of record.
- Community corrections: PRs against the mirror are ingested by the bot as
  staged proposals (with the contributor credited), never merged directly.
- A repo↔DB consistency check runs on every export and **blocks publish** on
  mismatch (v1 had a passive nightly assert; v2 makes it a gate).

## 3. Data model (Postgres, authoritative)

```
source(id, kind, url, adapter, schedule, slo_interval, last_hit_at)
document(id, source_id→source, url, r2_key, sha256 UNIQUE, fetched_at,
         doc_genre, ocr_quality)
claim(id UUID, document_id→document, field, value, value_num, unit,
      anchor_kind ENUM(quote,cell,figure), quote, page, bbox,
      match_score, confidence, extractor_version, status)
facility(id UUID, slug UNIQUE, name, aliases[], state, county, geo,
         developer, offtaker, status, flags[],
         first_permit_filed, first_power)          -- dates are derived, see §5
unit(id UUID, facility_id→facility, oem, model, count, mw_each, fuel,
     hours_permitted)
permit(id UUID, facility_id→facility, authority, permit_no, type, status,
       filed_at, issued_at)
event(id UUID, facility_id→facility, date, type, headline)
fact_provenance(fact_table, fact_id UUID, fact_field,
                claim_id→claim, note)               -- FK, not file:row:col
aggregate(id, metric, value, method, computed_at, inputs_note)
review(id, batch_date, pr_url, decision, corrections_json)  -- feeds evals
```

Every published fact row carries UUID identity from birth; provenance joins by
key, so rows can be reordered, exported, and diffed without breaking the chain
(fixes v1's fragile file:row:col addressing).

**Aggregates are facts too.** The hero number "announced GW" is computed from
our own `facility` rows in announced+ stages, with `method` and `inputs_note`
stored and shown. Third-party totals (Cleanview's 90 GW) may be *cited in
prose* with attribution, never rendered as our own counter (fixes v1's
weakest-provenance-at-highest-visibility flaw).

Time-series (gas hub prices, computed $/MWh histories — the v2 economics
layer) never enter git: they land as **parquet on R2**, queried with DuckDB at
build time. The mirror stays small and diffable forever.

## 4. Components

### 4.1 Watchers — treated as the hard part they are
Adapters are config + code with an explicit contract and **per-source SLOs**:

- `slo_interval`: expected max gap between successful finds; breach → staleness
  alert (a registry that "goes quiet" is presumed broken, not calm).
- Snapshot tests: each adapter ships a recorded fixture of its registry page;
  CI replays fixtures so registry redesigns are caught at the adapter, not in
  production silence.
- JS-heavy registries (TCEQ search, OPSB dockets) run on scheduled headless
  browser (Cloudflare Browser Rendering / Playwright in CI) — budgeted, not
  improvised. Index-less sources (county health depts) are polled as
  page-hash-diff watchers: any change → fetch + triage.
- Honest coverage math is published on the site (which states are watched, how,
  and since when) — coverage transparency is part of methodology.

### 4.2 Safety nets
EIA-860M monthly diff (eventual catch-all) · GDELT/news query set (candidates,
never facts) · CourtListener/RECAP litigation alerts · FERC eLibrary · EPA ECHO.

### 4.3 Fetcher + Archive
Unchanged from v1 (it survived the challenge): download → sha256 →
`r2://docs/{hash}` → `document` row. Immutable, dedup by hash, the only read
path downstream, the foundation of replay.

### 4.4 Extractor — anchoring v2 (fixes the OCR/table weaknesses)
Claims must be **anchored**, where anchor is one of:

- `quote` — verbatim span, validated by *normalized fuzzy match* (case/
  whitespace/ligature/OCR-confusable folding, similarity ≥ 0.92) instead of
  v1's brittle exact match;
- `cell` — table extractions anchor to page + bbox + reconstructed row/col
  headers (tables are first-class, not forced into fake quotes);
- `figure` — page + bbox for values read from stamped forms.

Plus **independent numeric validation**: every numeric claim's `value_num`
must re-locate in the document's number inventory (all numerals extracted
positionally) within unit-normalized tolerance. A "5 turbines" that matches
fuzzily but has no corresponding numeral on that page is rejected. Two layers
must agree for a number to pass — this closes the door fuzzy matching reopens.

Low `ocr_quality` documents route to a second extraction pass with a different
model; disagreement → flagged for human, never averaged.

Eval discipline: **every human correction in review is automatically appended
to the eval set** (`review.corrections_json` → labeled cases). Model or prompt
upgrades run the full eval suite and publish the score diff in the PR that
proposes the upgrade. The eval set grows as a byproduct of operating, not as a
chore (fixes v1's tiny-frozen-eval risk).

### 4.5 Normalizer + Resolver
Canonical enums (OEM/model aliases, statuses, fuels); deterministic joins
first (permit numbers, registration IDs, addresses); LLM-assisted alias
matching only *proposes*, with reasoning displayed in the review diff. Every
resolver merge/split decision is itself a fact with provenance, reversible by
ordinary review. Cross-agency identity (the LLC-per-site problem) is
acknowledged as permanently human-supervised at current scale — the design
goal is to make each decision cheap and auditable, not automatic.

### 4.6 Review gate — with the pressure valve designed in
Daily PR, quotes inline, 5–10 min. **Graduated auto-merge ships in v2.0
disabled by flag**, with rules already written:

- Auto-mergeable: anchored claims with match ≥ 0.97 AND numeric validation
  pass AND no status transition AND no new entity AND source adapter healthy.
- Always human: status transitions, new facilities, resolver merges, anything
  Island Watch touches, corrections.

When volume grows, flipping the flag changes review from "read everything" to
"read what matters" without redesign (fixes the review-bottleneck cliff).

### 4.7 Island Watch — verification promoted to a component (fixes the
structural blind spot)
True islands leave no registry trail — that's the point of being an island.
So "operating" evidence is collected from what islands *cannot hide*:

- **Thermal/visual satellite**: Sentinel-2 visual + Landsat/ECOSTRESS thermal
  anomalies over known sites; turbine exhaust and cooling signatures. Monthly
  cadence per watched site, on-demand after permit events. (SELC proved the
  method on xAI; we industrialize it.)
- **Gas nominations**: interstate pipeline nomination/flow postings (public
  under FERC rules) for delivery points serving known sites — fuel flowing is
  operation, fuel volume is a capacity-factor estimate (feeds the economics
  layer directly).
- Inspection/enforcement records, litigation filings, credible reporting.

Status state machine unchanged (announced → filed → permitted →
under_construction → operating + flags), but evidence classes are ranked
per-configuration: for grid-tied BTM, registries lead; for islands, Island
Watch leads. `operating` requires two independent evidence classes; press
releases remain inadmissible. Time-to-power stays computed, never entered.

### 4.8 Publisher — files are the canonical API (decided)
- Merge → export mirror → Pages build → `/data/*.csv`, `/api/v1/*.json`
  (static, CDN-cached, versioned by git tag). Zero servers, zero uptime
  obligations, graceful degradation by design.
- **REST facade decision rule (written into the architecture):** first
  concrete consumer request for query semantics → one-day Worker + D1 read
  facade over the same published snapshot; files remain canonical. Economics
  time-series v2 → parquet + DuckDB behind the same Worker. Never expose the
  operational Postgres publicly.
- **Hot lane (fixes breaking-news latency):** `btw hotfix` — single-event CLI
  path that stages one event + evidence, opens a one-line PR, and on merge
  publishes within minutes, with the full daily batch reconciling later. The
  day of an xAI ruling, we publish in minutes, not at tomorrow's batch.

### 4.9 Ops for one human
Dead-man switch on the daily cron (no heartbeat → alert) · per-adapter SLO
alerts · weekly digest auto-includes a pipeline-health footer (public honesty
doubles as self-monitoring) · runbook in repo · vacation mode = archive and
stage continue, publishing pauses, nothing is lost.

## 5. Failure modes → mitigations (v2)

| Failure | Mitigation |
|---|---|
| Store drift / broken provenance | Single Postgres truth, FK provenance by UUID, export gate blocks on mismatch |
| Registry redesign | Adapter SLOs + fixture snapshot tests + page-hash watchers |
| OCR breaks quote validation | Fuzzy-normalized anchors + independent numeric inventory validation + dual-model pass on low OCR |
| LLM invents values | Two-layer anchoring; unverifiable claims cannot become facts (schema-enforced) |
| Island operates invisibly | Island Watch: satellite/thermal + gas nominations as primary evidence for islands |
| Review bottleneck at 10× | Graduated auto-merge, rules shipped, flag-gated |
| Eval rot on model upgrades | Corrections auto-grow the eval set; upgrades require published eval diff |
| Breaking event beats the batch | Hot lane, minutes to publish |
| Hero number provenance | Aggregates computed from own rows with stored method; third-party totals quoted, never rendered as ours |
| Founder absent | Dead-man alerts; pipeline stages without publishing; replayable archive |

## 6. What we still deliberately do NOT build

Queues/Kafka · realtime infra · vector search · user accounts · API gateway ·
admin UI (PR view + Supabase console suffice) · orchestrators (cron + function
chain) · public write API. Each becomes worth revisiting only at ~100× current
volume, and none block the growth path below.

## 7. Growth path

v2.1 more state adapters (GA, PA, WY, NM, UT) · v2.2 flip auto-merge flag ·
v2.3 economics layer (gas parquet + heat-rate library + $/MWh with versioned
assumption sets — Island Watch gas flows feed capacity factors) · v2.4 Worker
read-facade on first consumer demand · v3 second vertical (industrial BTM) =
new adapters, same engine, same schema.

## 8. Build order (solo, AI-assisted)

1. Postgres schema + UUID/provenance core + export-to-mirror + publish — d1–3
2. Fetcher + R2 archive — d3–4
3. TCEQ + OPSB adapters with fixtures/SLOs — d4–6
4. Extractor with anchoring v2 + pilot-doc eval set — d6–9
5. Normalizer + staging + PR bot + merge webhook — d9–11
6. Remaining launch adapters + safety nets + dead-man ops — d11–13
7. Island Watch v0 (satellite tasking on 3 known sites + gas nomination
   watcher for Abilene/Southaven delivery points) — d13–15
8. Hot lane + digest drafter — d15–16

Genesis commit of the mirror = the pilot dataset, re-extracted through the
engine so day-one data already carries full anchored provenance.

## 9. Changelog v1 → v2 (what was wrong, what changed)

| v1 weakness (from design review) | v2 fix |
|---|---|
| Three stores of truth held together by convention | Postgres authoritative; git demoted to generated mirror; consistency check gates publish |
| file:row:col provenance breaks on reorder | UUID facts, FK provenance |
| Exact-quote validation fails on OCR/tables | Anchor kinds (quote/cell/figure), fuzzy-normalized match + independent numeric validation, dual-model on bad OCR |
| Verification blind to true islands | Island Watch component: satellite/thermal + gas nominations as first-class evidence |
| Human review = scaling cliff | Graduated auto-merge designed now, flag-gated |
| Watchers underestimated | Adapter SLOs, fixture tests, headless budget, page-hash watchers, public coverage map |
| Hero number = third-party data | Aggregates computed from own rows with stored method |
| Time-series would bloat git | Parquet on R2 + DuckDB; mirror stays small |
| Tiny frozen eval set | Corrections auto-feed evals; upgrades publish eval diffs |
| Batch latency on breaking news | Hot lane |
| Files-vs-REST ambiguity | Files canonical (decided); Worker+D1 facade on first demand, rule written down |
