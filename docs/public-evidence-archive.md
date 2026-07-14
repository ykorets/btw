# Public evidence archive

Every fetched document is preserved immutably in the private `btw-docs` R2
bucket under `docs/<sha256>.<ext>`. The original publisher URL remains the
canonical `Source` link.

Public access is fail-closed. Only hashes listed in
`engine/public_evidence.json` with a `public_record` or `licensed` rights basis
are copied to the separate `btw-evidence-public` bucket. Restricted satellite
scenes and third-party documents never become public merely because they were
ingested.

Deployment:

1. Create the `btw-evidence-public` R2 bucket.
2. Attach `evidence.behindthewatt.com` as its R2 custom domain.
3. Set the GitHub repository variable `BTW_ARCHIVE_BASE_URL` to
   `https://evidence.behindthewatt.com`.
4. Run `public-evidence-archive` first in dry-run mode, review the exact keys,
   then run it with writes enabled.
5. Run `publish-mirror` with scope `all` and rebuild Pages.

A dry run is safe before the public bucket exists: it verifies each immutable
source in the private archive and lists every object the write run will ensure.
It deliberately does not access the public bucket. The write run fails closed
until the separate public bucket and scoped credentials have been configured.

The public mirror exports both URLs, capture time, archive status and SHA-256.
Because object keys are content-addressed and responses use an immutable cache
policy, replacing an evidence file creates a new URL rather than mutating the
old receipt.
