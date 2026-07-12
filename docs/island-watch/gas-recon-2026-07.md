# Island Watch — gas nominations recon, July 2026

Goal (plan.md IW-v1): identify pipeline + delivery-point contracts for the
continuous "is it running" pulse between satellite snapshots. Result: one
island is capturable from a public FERC EBB today; one is honestly not.

## Southaven C2 (xai-colossus-2) — CAPTURABLE ✓

- **Pipeline: Texas Gas Transmission, LLC** (Boardwalk Pipelines, FERC
  interstate, TSP 100000). The site is a former Duke Energy plant parcel
  with legacy gas infrastructure — which explains the fast hookup.
- **Delivery meter: "MZX Southaven"** (entity at the meter: MZX Tech, the
  xAI subsidiary holding MDEQ permit 0680-00119), DeSoto County, MS. A
  third-party pipeline-flow tracker publicly reported adding this meter in
  Feb 2026 with "substantial flows delivering to MZX Southaven" — i.e. the
  meter exists, is named, and shows up in daily postings.
- **EBB**: Boardwalk migrated its informational postings from
  `infopost.bwpipelines.com` to **GasQuest** (`gasquest.com` — JS app;
  old infopost URLs redirect). Operationally-available / scheduled
  quantities are posted per NAESB cycle with a downloadable format.
- **Capture contract for the adapter**: daily OA/scheduled-quantities
  report for TSP 100000 → filter location name contains `MZX` →
  record scheduled/flowed quantity per cycle as an operation-pulse
  candidate. Flow ≈ 0 across cycles for N days = "not running" signal;
  sustained flow corroborates `operating`. Parser: needs a GasQuest
  probe (JS app — check for a stable CSV/JSON endpoint behind the UI
  before reaching for headless).

## Abilene (crusoe-stargate-abilene) — NO PUBLIC PULSE (honest limit)

- The on-site Longhorn Power Plant (TCEQ 177263, 11 units) receives gas
  via a **new private lateral** (built by Primoris as part of the Longhorn
  project — "gas pipeline lateral" is listed in their project scope).
- The campus's stated supply direction is a **private 42-inch Permian
  Basin line** ("would support 6+ GW of gas generation, private capital"
  — Lancium/ESIG deck, May 2025). Private + intrastate Texas systems
  (candidates for the current tap: Atmos Pipeline–Texas, ONEOK WesTex)
  carry **no FERC EBB obligation** — no public daily postings exist.
- Conclusion: Abilene's "is it running" pulse stays satellite-first
  (S2 change detection + Wayback passes, power block now localized at
  615 FM 2404). Revisit if (a) the 42" line files as a FERC carrier,
  (b) a commercial flow-data license is bought, or (c) RRC filings start
  exposing usable throughput.

## Memphis C1 (xai-colossus-1) — out of scope this pass

C1's turbines sit inside MLGW territory (LDC city-gates fed by Texas Gas
Transmission / Trunkline). LDC aggregation makes a site-specific pulse
unlikely from EBB data alone; not pursued for v1.

## Sources

- Pipeline-flow tracker post naming the MZX Southaven meter on Texas Gas
  Transmission (LinkedIn, Feb 2026)
- Boardwalk / Texas Gas Transmission: bwpipelines.com, GasQuest postings
- Primoris Longhorn Power Plant project sheet (prim.com) — lateral scope
- Lancium/ESIG "Stargate Abilene" deck (May 2025) — 42" private line
- WIRED / DCD / SELC / Earthjustice coverage of Southaven turbine counts
  (27 → 46 units, 495 MW+; MDEQ permit 41 units / 1.2 GW)
