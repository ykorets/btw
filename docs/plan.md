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

- [x] Supabase: dedicated project `btw` (do not reuse KPI project)
- [x] Cloudflare R2: bucket `btw-docs` + API token (read/write)
- [x] ~~GitHub: repo `btw-data`~~ superseded: mirror lives on the `mirror`
      branch of `btw` (M5 design deviation, see M5 status)
- [x] Shiva GitHub App on `btw`: Contents + Pull requests + Workflows,
      read/write (workflows permission added 2026-07-12)
- [x] Actions secrets in `btw`: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`,
      `R2_ACCOUNT_ID`, `R2_ACCESS_KEY`, `R2_SECRET`, `ANTHROPIC_API_KEY`,
      `GEMINI_API_KEY`, `COURTLISTENER_TOKEN` (added 2026-07-12)
- [x] `HEARTBEAT_URL` → self-hosted Healthchecks ("Watchtower",
      hc.kpicreatives.com via Coolify; infra/healthchecks/) — closed
      2026-07-13, first ping received from daily-intake #18

**M0 CLOSED 2026-07-13.** Nothing below is blocked anymore.

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

## M3.5 — Quorum + anchoring upgrades (follow-ups from M3/M5)

Build: cross-page quote matching (claimed page first; page±1 span rescues
quotes straddling a break, numeric check runs against the matched span) ·
quorum per D9 (`--quorum`): cross_checker second family → agree/solo auto-
accept, same-passage contradiction → one escalation pass, surviving
contradiction held at status `extracted` for human review; verdict recorded
in `claim.quorum` (005) · sentence-context binding in normalize: quote
without a model token binds iff exactly one unit's model appears in the
fuzzy-located window around the quote in the archived page (R2); ambiguity
never binds · per-model evals: `extract --role X`, `extractor_version`
stamped `extract-v2[+quorum]@model`, `evalrun --compare` prints per-version
recall/precision + ledger cost per model.

DoD: the two M3 appeal misses (unit.count 15, SMT-130) recovered by the span
fallback; Titan mw_each 38 binds via context; quorum run on all genesis docs
with verdicts recorded; per-model table exists to (re)pick role assignments.

**Status 2026-07-12 — DONE (run #6 green, quorum live). Recall 13/13.**
All four pieces implemented with pure-function tests (32 green): span
fallback keeps the claimed page authoritative and flags `[quote spans page
break ±1]` in entity_hint; quorum policy distinguishes contradiction (same
passage via quote-overlap ≥0.85, different value) from coverage variance
(absence → solo, anchor already vouched); escalation fires once per doc and
only on contradiction — cost-bounded; checker/escalation claims are never
stored, only their ledger rows. Context binding is deterministic and
R2-optional (creds absent → binding off, quote-token path intact).
Live run (005 applied, extract-genesis #6, quorum=true): **recall 13/13
(100%)** — both M3 appeal misses recovered (unit.count 15 validated match
1.0 quorum=agree; SMT-130 validated), page-break flag observed working on
two SELC claims; verdicts 70 agree / 32 solo / 0 disagree (no escalation
needed), 2 anchor-rejects — the guard still bites. Cost ≈ $0.33/quorum-run
(4 Gemini primary + 4 Haiku checker), under the $1 DoD.

**Follow-ups closed 2026-07-13.** Per-model table (D8): Gemini Flash
13/13 recall / 98% precision vs Haiku 4.5 10/13 / 100% precision (66/66
validated, zero anchor-rejects — extracts less, never invents);
models.yaml roles confirmed as-is: Gemini = extractor (coverage), Haiku =
checker (its agree is worth a lot precisely because it rarely volunteers).
Titan mw_each 38: context binding correctly REFUSED the bind — the ±240
window contains both models (amendment items sit adjacent) and "ambiguity
never binds" held; resolved by reviewer via elimination (LM2500 explicitly
took 34.1), staged with provenance note, merged in review PR #26 —
Abilene now published at 5×38 + 5×34.1 = 360.5 MW, fleet 1.03 GW. The
deterministic layer hands multi-model windows to the human by design.

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

**M4 DoD fully closed 2026-07-13.** The last open item — "missed-heartbeat
alert observed once on purpose" — tested live against Watchtower
(self-hosted Healthchecks): /fail signal → check DOWN → Telegram alert
received → success ping → UP-again alert. The dead-man switch works
end-to-end; daily-intake pings it on every green run.

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

**M6 CLOSED 2026-07-13 (daily-intake #18 green, 10/11 sources live).**
All stubs resolved, each probed live before coding: FERC eLibrary turned
out to front a keyless JSON API (POST eLibraryWebAPI/api/Search/
AdvancedSearch; 12 candidates first sweep incl. Data Center Coalition
intervention in EL26-72) · MDEQ needed no JS after all — the enSearch
"EPD Permits at Public Notice" page is server-rendered ASPX (33 candidates
first sweep; kw-hits: Entergy Traceview Advanced Power Station
Air-Construction PSD + TVA Magnolia Combined Cycle) · ECHO discovery fixed:
the working NAICS param is p_ncs (p_naics silently ignored → the 279k
wall); p_ncs=518210 → 460 CAA facilities nationwide, counter-hash now
catches any new data-center air permit in the country · OPSB = honest
limit: DIS's F5 WAF serves browsers but rejects datacenter IPs, no RSS;
SLO relaxed to 30d, coverage via gdelt + ferc + weekly manual glance.

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

**Status 2026-07-12 — DoD 3/3 ✓; gas recon done, parser is the last v1 gap.**
`iw-sentinel` adapter live: Earth Search STAC (keyless, AWS open data), new
S2 L2A scenes over the 3 site AOIs → candidate with public preview thumbnail
(reviewer eyeballs it from the review PR); cloud-cover filter; spectral
change detection over the AOI (windowed COG reads) → v1.1. Evidence classes
on facility.status, all three facilities at 2 classes: xai-colossus-1 ✓
(IW-v0 satellite + appeal-filing claims); xai-colossus-2 ✓ (MDEQ PSD
0680-00119 archived + extracted — 41-unit envelope in 4 unit groups — plus
IW-v0 satellite module count); crusoe-stargate-abilene ✓ (power block
localized at 615 FM 2404 via TCEQ RN112029061 — ~10–11 packages, consistent
with the permitted 11 — plus TCEQ 177263 regulatory claims). Gas recon
(docs/island-watch/gas-recon-2026-07.md): Southaven is capturable — Texas
Gas Transmission (Boardwalk, TSP 100000), delivery meter "MZX Southaven"
confirmed in daily postings; EBB migrated to GasQuest, adapter contract
documented, parser probe = remaining work. Abilene has NO public pulse
(private lateral + planned private 42" Permian line, intrastate candidates
carry no FERC EBB obligation) — published as an honest limit; pulse stays
satellite-first. Scene-purchase list unchanged (2 × ~$15–30, 2026-Q3).

**Gas pulse LIVE 2026-07-13.** GasQuest turned out to be a clean JSON API
(list: POST infopost/infopostdetails; download: GET infopost/postings?
postingsDocumentId → base64 NAESB CSV). `gas_ebb` adapter live: newest
Operational Capacity posting → rows for watched locations → one candidate
per (location, gas day); ZERO scheduled flow sets kw_hit — silence at a
watched island surfaces in the review PR. First live capture: Loc 26096
"MZX Southaven", gas day 20260713, 177,000 MMBtu scheduled (~7.4k MMBtu/h
≈ several hundred MW of simple-cycle equivalent — the island is running
hard). IW-v1 fully closed: permits + satellites + gas flows all ticking.

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

**Hotfix<15min DoD — CLOSED 2026-07-12 with the first real event.**
Southaven 57-turbine court record (NAACP v. xAI Doc 52, anchored claim
observation.unit_count=57 match 1.0) ran the full hot lane: dispatch
20:54:50 UTC → event staged + review PR refreshed <2 min → human merge
21:16:28 → review-promote 13 s → mirror events.json carried the event by
~21:17. Machine segments total <3 min; the 20-minute human review gap is
the design, not latency. First real event end-to-end.

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
