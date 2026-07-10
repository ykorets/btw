# btw engine

Pipeline: watch → fetch/archive → extract (anchored claims) → normalize/resolve
→ review (PR gate) → publish (files-as-API). See ../docs/architecture.md.

Runtime: Python 3.11+, scheduled by GitHub Actions (docs/decisions.md · D3).
Storage: Supabase Postgres (schema/) · Cloudflare R2 (archive) · btw-data (mirror).

Modules map 1:1 to architecture components:
watch.py · fetch.py · extract.py · normalize.py · review.py · publish.py · island_watch.py
