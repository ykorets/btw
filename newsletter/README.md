# BTW Weekly delivery

The newsletter uses two separate systems:

- this Cloudflare Worker owns subscription and double opt-in;
- the engine builds a deterministic Tuesday edition from the published record;
- this Worker creates the Resend draft and enforces editor approval;
- Resend owns confirmed contacts, delivery and unsubscribe handling.

An address is not created as a Resend contact until its confirmation link is opened.
The Monday workflow never sends to subscribers. It excludes staging facts and watcher
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

The `DIGEST_STATE` KV binding stores edition id, content hash, review window,
Resend broadcast id and delivery state. It does not contain subscriber addresses.

## Monday review, Tuesday delivery

At 13:00 UTC every Monday, `.github/workflows/digest.yml`:

1. validates the Resend Segment and KV binding;
2. asks the Worker for the end of the last editor-approved edition so a missed
   or rejected run creates no gap;
3. reads published changes from Supabase;
4. renders deterministic HTML, text and Markdown without an LLM;
5. asks the Worker to create a Resend draft;
6. emails the exact preview only to the configured editor;
7. uploads `digest/YYYY-MM-DD.{md,json,html}` as a private workflow artifact.

The preview links to the current Resend draft and to a review page. Opening a
link is read-only. Only the review page's POST confirmation schedules the
Broadcast for Tuesday at 13:00 UTC; approval after that time requests immediate
delivery. Without approval, the subscriber Segment is never contacted.

The Worker rejects a repeated issue id with different content. A retry of the
same content retrieves the existing Resend Broadcast instead of creating a
second one.

The workflow can also be run manually:

- `dry-run` (default) renders downloadable receipt files and sends nothing;
- `validate` checks credentials, Segment and delivery state only;
- `review` creates the production draft and sends its preview only to the editor.

Every Broadcast includes Resend's per-recipient unsubscribe URL. Resend applies
the unsubscribe state automatically before future Broadcasts.

## Local verification

```sh
npm test
npm run type-check
```
