# Local validation and CI cost

Run the source validation commands from `.github/workflows/ci.yml` locally before marking a PR ready. Use the same locked toolchain and synthetic disposable database images. Do not run production/publish/newsletter workflows as tests. Preserve the SQL regression, deterministic artifact and mirror compatibility checks for changes that affect them.

Use focused Python/SQL tests while iterating. Batch reviewable changes and keep unfinished work in a draft PR. Only main push and ready PR events run general CI; feature pushes no longer duplicate PR checks. Superseded validation is cancelled; scheduled business work and deployment workflows retain their existing triggers.

A skipped draft check is not release evidence. Mark ready and require the actual current candidate's checks before merging. Record commands and source SHA in the PR. These public standard runners are not claimed to account for the private Actions invoice.

Lighthouse is only for public indexable SEO pages. Never run it against internal applications, dashboards, authenticated routes, previews or noindex pages; use functional, accessibility and access-control checks for those surfaces.
