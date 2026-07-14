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
        Cloudflare Worker → Resend Broadcast → BTW Weekly Segment
                 │
                 ├─ KV: issue id / hash / broadcast id / state
                 └─ Git: digest/YYYY-MM-DD.{md,json}
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
only then calls the send endpoint. A retry first retrieves that id and status.
This closes the common “send succeeded but workflow crashed” duplicate path.

The issue id is the Tuesday UTC date. The Worker rejects the same issue id
with a different content hash. If KV state is lost, it searches recent Resend
Broadcasts for the unique internal name before creating another draft.

Resend Broadcasts include `{{{RESEND_UNSUBSCRIBE_URL}}}`; Resend owns contact
suppression and unsubscribe handling.

## Scheduling and recovery

- Schedule: Tuesday at 13:00 UTC.
- Scheduled runs always send; manual runs default to dry-run.
- The start cursor is the prior archived edition's `window.end`, not simply
  “now minus seven days”, so a missed Tuesday is recovered without a gap.
- A successful send archives the edition Markdown and JSON receipt on `main`.
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
