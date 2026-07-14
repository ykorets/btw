# BTW Weekly delivery

The newsletter uses two separate systems:

- this Cloudflare Worker owns subscription and double opt-in;
- the engine builds a deterministic Tuesday edition from the published record;
- this Worker creates/sends the Resend Broadcast exactly once;
- Resend owns confirmed contacts, delivery and unsubscribe handling.

An address is not created as a Resend contact until its confirmation link is opened.
The Tuesday workflow sends automatically. It excludes staging facts and watcher
candidates; exact unit, permit and event changes come from sealed review
manifests. Published third-party announcements are shown in a separate,
explicitly unverified section.

## One-time production setup

1. Create a Resend account and verify the sending domain `updates.behindthewatt.com`.
2. Create a Segment named `BTW Weekly`.
3. In this directory, add Worker secrets without writing them to a file:

   ```sh
   npx wrangler secret put RESEND_API_KEY
   npx wrangler secret put RESEND_SEGMENT_ID
   npx wrangler secret put SIGNING_SECRET
   npx wrangler secret put DIGEST_TRIGGER_SECRET
   ```

   `SIGNING_SECRET` should be a newly generated high-entropy value of at least 32 bytes.

4. Deploy with `npm run deploy`. The Worker claims `newsletter.behindthewatt.com` as its custom domain.
5. Submit a test address on the site, confirm it, and verify that it appears in the `BTW Weekly` Segment.

Worker secrets remain attached to the deployed Worker when GitHub Actions publishes a new version.

Add the same `DIGEST_TRIGGER_SECRET` value as a GitHub Actions secret. The
Resend API key stays only in the Worker; the digest workflow never receives it.

The `DIGEST_STATE` KV binding stores only edition id, content hash, Resend
broadcast id and delivery state. It does not contain subscriber addresses.

## Automatic Tuesday delivery

At 13:00 UTC every Tuesday, `.github/workflows/digest.yml`:

1. validates the Resend Segment and KV binding;
2. finds the end of the last archived edition so a missed run creates no gap;
3. reads published changes from Supabase;
4. renders deterministic HTML, text and Markdown without an LLM;
5. asks the Worker to create a draft, persists its id, then sends it;
6. commits `digest/YYYY-MM-DD.{md,json}` as the public delivery receipt.

The Worker rejects a repeated issue id with different content. A retry of the
same content retrieves the existing Resend Broadcast instead of creating a
second one.

The workflow can also be run manually:

- `dry-run` (default) renders downloadable receipt files and sends nothing;
- `validate` checks credentials, Segment and delivery state only;
- `send` uses the production Segment and is protected by the same exactly-once
  issue id/content hash contract as the schedule.

Every Broadcast includes Resend's per-recipient unsubscribe URL. Resend applies
the unsubscribe state automatically before future Broadcasts.

## Local verification

```sh
npm test
npm run type-check
```
