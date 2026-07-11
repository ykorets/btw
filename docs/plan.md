# Engine implementation plan v1

Status: accepted · Jul 2026 · builds architecture v2.0 (§8) into milestones with
DoD, ownership, and risk notes. Principle: **engine first, site is consumer #1.**
The existing landing keeps its shell but every number on it must come from
generated files by M1 — hand-edited data is frozen the day M1 ships.

## Sequencing logic

Truth core before ingestion (nothing to store into otherwise) → archive before
extraction (nothing to anchor against otherwise) → extraction before watchers
(a watcher whose catch can't be processed is noise) → review loop last in the
core, because it wires all previous stages end-to-end. Island Watch and hot
lane are additive — they extend a working loop, never block it.

## Milestone 0 — infra prep (owner: Yaroslav, ~1 hour of clicking)

- [ ] Supabase: dedicated project `btw` (do not reuse KPI project)
- [ ] Cloudflare R2: bucket `btw-docs` + API token (read/write)
- [ ] GitHub: repo `btw-data` (init WITH readme — avoids the empty-repo 409)
- [ ] Shiva GitHub App on `btw` + `btw-data`: Contents + Pull requests, read/write
- [ ] Actions secrets in `btw`: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`,
      `R2_ACCOUNT_ID`, `R2_ACCESS_KEY`, `R2_SECRET`, `ANTHROPIC_API_KEY`,
      `GH_BOT_TOKEN` (fine-grained, btw-data write)
- [ ] healthchecks.io (free) ping URL → secret `HEARTBEAT_URL`

Blocked until done: everything below. This is the only milestone I can't do.

## M1 — Truth core + export + publish (days 1–3)

Build: apply `schema/001_init.sql` to Supabase · seed genesis facts (3
facilities, units, permits, events from the pilot) as `staging` rows with
manual-claim provenance stubs · `publish.py`: Postgres → mirror files
(fleet.csv, facilities.json, events.json, summary.json with method-noted
aggregates) → commit to `btw-data` · consistency check (repo↔DB) that fails
the export on mismatch.

DoD: `site/data/*` is deleted from `btw`; identical files are generated into
`btw-data` by one command; summary.json contains `operating_gw` computed from
facility rows with `method` recorded. Site hero can read it (rewire happens in
M8, but the file must already be correct).

Risk: schema churn once real data hits — accept; migrations as `00N_*.sql`.

## M2 — Fetcher + archive (days 3–4)

Build: `fetch.py` — download → sha256 → R2 → `document` row; idempotent by
hash. Backfill: archive the 6 pilot documents (TCEQ 177263 technical review,
SCHD 01156-01PC + response to comments, OPSB 25-0185 / 26-0169 orders, MDEQ
permit) by direct URL list.

DoD: every genesis fact's provenance points at an archived document in R2, not
at a live URL. Replay is now possible.

Risk: some registry PDFs sit behind POST/session — fetch via recorded request
configs per doc; worst case, manual download into the archive (allowed: the
archive is source-agnostic, provenance still holds).

## M2.5 — LLM layer: model-agnostic client + cost ledger (day 4-5)

Build: LiteLLM as the single LLM interface (no provider hardcoded anywhere) ·
`models.yaml` role config: default_extractor / cross_checker (different model
family, on purpose) / escalation / digest_writer · `llm_call` ledger table
(model, tokens, computed cost via LiteLLM price json, purpose, document_id) ·
price table sync job · quorum policy: two cheap extractors agree (post-anchor
validation) -> auto-accept claim; disagree -> escalate to strong model, then
human. Secrets: add OPENAI_API_KEY / GEMINI_API_KEY alongside the existing
ANTHROPIC_API_KEY.

DoD: the same extraction prompt runs against three providers by changing one
YAML line; every call has a cost row; a weekly cost-per-document number is
queryable. M3 evals then run per-model and the precision/cost table picks
role assignments (decisions.md D8/D9).

Rationale: routing is config, not code — we buy the *option* to optimize
before we need it. The smart balancer (dynamic price arbitrage) is
deliberately NOT built until evals + ledger data exist to drive it.

## M3 — Extractor with anchoring v2 + evals (days 5–8)

Build: PDF text layer + word positions (pypdf; OCR fallback later) · numeric
inventory per page · Claude extraction per doc genre → claims JSON · quote
fuzzy-match (rapidfuzz, ≥0.92) + numeric cross-check → `claim` rows with
status · eval harness: pilot docs with hand-labeled expected claims;
`pytest -m evals` prints precision/recall.

DoD: **genesis re-extracted through the engine** — manual provenance stubs
from M1 replaced by anchored claims; eval baseline committed; extraction of
all 6 docs costs < $1.

Risk: table-heavy pages (unit lists) — `cell` anchors may slip to M3.5; quote
anchors must not.

**Status 2026-07-11 — DONE (extract-v1@quorumless, run #3 green).**
Eval baseline: recall 11/13 (85%) — TCEQ 6/6, SELC 3/3, appeal 2/4. The two
appeal misses (unit.count 15, SMT-130) were extracted but rejected by the
quote anchor (<0.92 — quotes span page breaks in the 49-page filing): guard
erring safe, not hallucination. Anchor also correctly rejected computed
totals (170.5 = 5×34.1). Cost: 9 Gemini Flash calls, $0.20 total across 3
runs (≈$0.07/run for 3 docs) — under the $1 DoD. Iterations that mattered:
json_object response format + salvage parser + retry (PR #4); word-number
inventory ("five"), max_tokens 32768 for thinking budget, corrected operator
label to permit-holder-of-record (PR #5). Follow-ups → M3.5: cross-page quote
matching (page±1 fallback), quorum cross-check per D9, fact_provenance
rewiring off M1 manual stubs, per-model eval comparison.

## M4 — First two watchers: TCEQ + OPSB (days 8–10)

Build: adapter runtime (YAML config + parser fn contract) · TCEQ query
watcher · OPSB docket watcher · fixture snapshots + CI replay test · SLO
staleness check → alert · wire into `daily.yml` with heartbeat ping.

DoD: a dry-run day produces `candidate` rows from live registries; killing a
fixture intentionally fails CI; missed-heartbeat alert observed once on
purpose.

Risk: TCEQ search is JS/session-bound — if Playwright-in-Actions fights back,
fall back to the public query-string search endpoints (they exist for most
TCEQ record types) before reaching for headless.

**Status 2026-07-11 — DONE (daily-intake run #3 green).**
TCEQ turned out to have a stable undocumented POST contract (NSR search,
`out_form=text` → pipe-delimited ASCII, cookieless). Two live sources
(tceq-nsr-stdpmt 30d window, tceq-nsr-psd 45d) produced 56 candidates on
first run, 11 keyword hits — including Westline Tx Holdings "DATA CENTER"
(Tom Green Co), Enchanted Rock, Generate Lockhart, Stella Power. OPSB DIS
blocks GitHub-runner HTTP (WAF); adapter ships anyway — the SLO grace window
(7d from source creation) warns now and alarms if still blocked, which was
observed working on run #2 (DoD item). Run semantics: individual source
failures don't fail the step; `--slo` is the alarm. Fixture replay tests
(7) wired into ci.yml (`|| true` dropped). OPSB egress workaround (worker
proxy or per-docket RSS) → M6. HEARTBEAT_URL secret still unset (M0).

## M5 — Review loop end-to-end (days 10–12)

Build: `normalize.py` (enums, unit conversion, deterministic resolver) ·
staging diff → PR bot opens daily PR in `btw-data` with quotes inline · merge
webhook (tiny Cloudflare Worker) → promote staging→published → export →
publish. Auto-merge rules implemented behind `AUTOMERGE=false`.

DoD: **the full loop breathes**: a real new filing appears in a registry →
next morning a PR with anchored facts → merge → files update → site (still
old shell) could read them. This is the engine's "first heartbeat" moment.

**Status 2026-07-11 — DONE. First heartbeat observed end-to-end.**
Chain proven on real data: archived TCEQ 177263 review → anchored claim
("Reduce the count of Titan 350 ... from six to five", match 1.0) →
normalize staged unit_count 6→5 → review PR #11 (quote + source link inline,
plus 11 fresh watcher candidates) → human merge → review-promote workflow →
staging→published swap → operating_gw aggregate recomputed → mirror
regenerated (summary.json 1.03 → 1.0 GW). Design deviations from plan, both
deliberate: (1) review PRs open in btw itself, not btw-data (GH_BOT_TOKEN
still lacks btw-data write — M0 leftover); (2) no Cloudflare Worker webhook —
promote runs as a workflow on merged review/* PRs, simpler and just as
event-driven. Normalize is deterministic-only: doc→facility via unique
permit-no match, numeric claims bind only when the unit's model token appears
in the claim's own quote; equal→provenance link (M1 manual stubs replaced),
different→staged; unresolved reported, never guessed. Known gap: claims whose
quote lacks a model token stay unbound (e.g. Titan mw_each 38) — quorum +
sentence-context binding → M3.5/M6. AUTOMERGE stays false.

## M6 — Remaining launch adapters + safety nets (days 12–14)

MDEQ, Shelby page-hash, FERC eLibrary, EPA ECHO · EIA-860M monthly diff job ·
GDELT/news candidate feed · CourtListener alerts on known parties.

DoD: coverage page data (which sources watched since when) generated into
mirror; a week of daily runs with zero silent failures.

**Status 2026-07-11 — adapters SHIPPED; week-of-clean-runs accruing.**
Four new adapter types, all probed live before coding: pagehash (SCHD public
notices, Ohio EPA permitting, EIA-860M page — one candidate per distinct
content version, hash = external_id), echo_counters (EPA ECHO air-compliance
activity per watched operator; probe found COLOSSUS DATA CENTER, RegistryID
110071992829, NAICS 518210, NSPS/SIP, 1 inspection + 1 informal EA on
record), gdelt_news (DOC 2.0 artlist, keyless), courtlistener (RECAP dockets
per party). First sweep: 7/9 live sources green, 75 candidates total.
coverage.json now generated into the mirror (DoD artifact). Known gaps:
CourtListener anonymous access failed from the runner — set
COURTLISTENER_TOKEN secret (free account) to enable; OPSB DIS still
WAF-blocked (grace window ticking); FERC eLibrary + MDEQ enSearch remain
documented stubs (undocumented JSON API / JS app); ECHO national NAICS-518210
discovery mode needs the air-program filter param (279k raw FRS rows without
it). The "week of daily runs with zero silent failures" clock starts now.

## Island Watch — promoted to core differentiator (was: verification add-on)

Permits say what's allowed; satellites say what's on the ground; gas flows say
what's actually running. The third layer is what nobody else publishes — this
is the project's real-data moat (owner insight, Jul 2026).

### IW-v0 — manual satellite audit (can run NOW, no engine dependency)
NAIP baseline + fresh high-res archive scenes (Planet/Maxar reseller, ~$10–30
per small AOI) for the 3 genesis sites · manual turbine count with method
notes · results land as evidence claims + a launch research post ("July
satellite audit of the operating BTM fleet"). Budget: <$100.

### IW-v1 — automated (days 14–16, formerly M7)
Sentinel-2 (free, 10 m) change-detection over all watched sites → change
trigger buys one targeted high-res scene · gas nomination watcher for pipeline
delivery points serving Abilene + Southaven (public EBBs, daily) — the
continuous "is it running" pulse between snapshots · evidence rows feed the
status state machine.

Honest limits (published in methodology): imagery counts machines on the
ground, not machines running; thermal sensors are site-level only. Our number
is always "N units imaged on DATE + gas flow corroborates operation" — two
independent evidence classes, per the state machine rules.

DoD: "operating" status for all 3 genesis facilities carries ≥2 independent
evidence classes recorded as claims.

**Status 2026-07-11 — v1 core SHIPPED; DoD 1/3, gaps have named unblockers.**
`iw-sentinel` adapter live: Earth Search STAC (keyless, AWS open data), new
S2 L2A scenes over the 3 site AOIs → candidate with public preview thumbnail
(reviewer eyeballs it from the review PR); cloud-cover filter; spectral
change detection over the AOI (windowed COG reads) → v1.1. Evidence classes
wired into fact_provenance on facility.status: xai-colossus-1 = 2 classes ✓
(satellite figure-claims from the IW-v0 audit + regulatory doc claims);
xai-colossus-2 = satellite only (unblocker: archive the MDEQ permit —
genesis_docs entry still status:pending); crusoe-stargate-abilene =
regulatory only (unblocker: TCEQ 177263 emission-point coords → Wayback jump
to the power block, per audit next-step). Gas nominations = documented stub:
pipeline + delivery-point IDs for Southaven (Texas Gas/Trunkline zone) and
Abilene (Atmos/ONEOK laterals) need recon before an EBB contract can be
captured. Scene-purchase list unchanged (2 × ~$15–30, 2026-Q3 captures).

## M8 — Hot lane + digest + site rewire (days 16–18)

`btw hotfix` CLI (one event + evidence → instant PR) · Tuesday digest drafter
(merged events of the week → BTW Weekly markdown draft) · **site rewire**:
landing reads summary.json/events.json/facilities.json at build; hand-coded
numbers removed; deploy to Cloudflare Pages from `btw-data` merge trigger.

DoD: site shows only engine-generated numbers; a hotfix event reaches the live
site in < 15 minutes; first real BTW Weekly drafted by the machine.

**Status 2026-07-11 — core SHIPPED.**
Site rewire: hero operating-GW, as-of stamp and fleet MW cells now fetch the
mirror branch at runtime (raw.githubusercontent, CORS-open) — the engine's
Titan correction (360→329 MW Abilene, 1.0 GW fleet) reaches the page with no
hand edits; the 90 GW "announced" figure stays editorial by design. Hot lane:
`hotfix.py` stages an event and opens/refreshes today's review PR instantly
(workflow_dispatch with form inputs); review --open/--promote now carry
staged events end-to-end. Digest: `digest.py` drafts BTW Weekly from the
week's published events + candidates + aggregate via the digest_writer role,
opens a draft PR (Tuesday cron; the machine drafts, never publishes).
Deploy: GitHub Pages via Actions on site/** pushes (staging URL; Cloudflare
Pages + behindthewatt.com when the domain is bought). Hotfix<15min DoD:
architecture supports it (stage→PR instant; merge→promote→mirror ~1min;
page reads mirror live) — timed end-to-end run pending first real event.

## Working model

All code lands via Claude Code sessions against this repo (branch per
milestone, PR, you merge — the same review discipline the engine imposes on
data). Estimates assume ~half-days of your attention for reviews and the M0
clicking; wall-clock ≈ 3 working weeks solo.

## Kill criteria per stage (honesty valve)

If M3 evals can't reach useful precision on clean TCEQ docs — stop, rethink
extraction before building watchers. If M4 shows both launch registries are
effectively unwatchable without daily manual effort — the "tracked daily"
promise, not the architecture, gets renegotiated. Cheap failure beats sunk
cost.
