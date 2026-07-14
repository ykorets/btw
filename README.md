# behind│the watt

**The open database of behind-the-meter data center power.**
Who's actually online, how fast they got there, and what their power really costs.
Tracked daily. Sourced from air permits, not press releases.

- Site: `site/` — Astro static build → behindthewatt.com
- Engine: `engine/` — watchers → archive → LLM extraction → review → publish
- Data mirror: generated read-only export, will live at `behindthewatt/data` (own repo)
- Architecture: [docs/architecture.md](docs/architecture.md) · decisions: [docs/decisions.md](docs/decisions.md)

## Licenses
- Code: MIT ([LICENSE](LICENSE))
- Data: CC BY 4.0 ([LICENSE-DATA.md](LICENSE-DATA.md)) — cite as *Behind the Watt, behindthewatt.com*

## Status
Pre-launch. Genesis dataset: 3 verified operating facilities, pilot of Jul 2026.
