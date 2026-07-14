# BTW Weekly automation

## Decision

BTW Weekly is a deterministic view of the published database record. It is
not a second extraction or editorial pipeline, and it does not use an LLM to
invent narrative between facts.

```text
Supabase published facts
  ├─ sealed review manifests → unit / permit / event changes
  ├─ published facility.updated_at changes
  └─ published announcement.updated_at changes (separate label)
                 │
                 ▼
        digest.py renders HTML + text + receipt
                 │ authenticated issue_id + content hash
                 ▼
        Cloudflare Worker → Resend draft → editor preview
                 │
                 ├─ GET review page (never sends)
                 ├─ POST approval → Tuesday Resend delivery
                 └─ KV: issue / hash / window / broadcast / state
```

## Truth boundary

- Unit, permit and event changes must be exact rows in a promoted, immutable
  review manifest.
- Facility records must already be `fact_state=published`.
- Announcement records must already be `fact_state=published`, remain under a
  third-party label, and never contribute to verified operating capacity.
- Staging rows and watcher candidates are never email content.
- “What it means” is derived only from the stored operating aggregate and its
  prior-window value. If no comparable baseline exists, the email says so.

## Delivery contract

The Worker creates the Broadcast as a draft, stores its Resend id in KV, and
emails the preview only to the editor. It never calls the Broadcast send API
from the scheduled workflow. A retry retrieves the existing id and does not
create another draft or preview email.

The email link opens a read-only review page because mail security scanners may
follow links automatically. Only a separate POST form can approve delivery.
Approval schedules the current Resend draft for Tuesday at 13:00 UTC, preserving
any edits made in Resend. The approval call uses a stable idempotency key and
re-checks Resend state before retrying.

The issue id is the Tuesday UTC date. The Worker rejects the same issue id
with a different content hash. If KV state is lost, it searches recent Resend
Broadcasts for the unique internal name before creating another draft.

Resend Broadcasts include `{{{RESEND_UNSUBSCRIBE_URL}}}`; Resend owns contact
suppression and unsubscribe handling.

## Scheduling and recovery

- Review schedule: Monday at 13:00 UTC. Approved delivery: Tuesday at 13:00 UTC.
- Scheduled runs create a review only; manual runs default to dry-run.
- The start cursor is the last editor-approved delivery window, not simply
  “now minus seven days”, so a missed or rejected edition is recovered without
  a gap.
- Markdown, JSON and HTML previews are retained as GitHub workflow artifacts.
- An unapproved edition never advances the delivery cursor.
- A quiet week still sends an honest edition stating that no sealed review was
  promoted; this matches the weekly promise without manufacturing news.

## Secrets and data exposure

- `RESEND_API_KEY`, `RESEND_SEGMENT_ID` and `SIGNING_SECRET` exist only in the
  Worker.
- `DIGEST_TRIGGER_SECRET` exists in the Worker and GitHub Actions.
- GitHub Actions has read access to Supabase through the existing service key,
  but the digest code performs GET requests only.
- KV stores no email addresses and no database rows.

## Growth path

At the current weekly cadence and audience size, GitHub Actions + one Worker +
KV is intentionally small. Revisit queues and a dedicated edition table only
if multiple publications, multiple sends per week, per-subscriber
personalization or transactional delivery analytics become requirements.
