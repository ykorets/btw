# BTW Weekly delivery

The newsletter uses two separate systems:

- this Cloudflare Worker owns subscription and double opt-in;
- Resend owns confirmed contacts, Broadcasts and unsubscribe handling.

An address is not created as a Resend contact until its confirmation link is opened. The existing Tuesday digest job still creates a GitHub PR; merging a draft does not send an email.

## One-time production setup

1. Create a Resend account and verify the sending domain `updates.behindthewatt.com`.
2. Create a Segment named `BTW Weekly`.
3. In this directory, add Worker secrets without writing them to a file:

   ```sh
   npx wrangler secret put RESEND_API_KEY
   npx wrangler secret put RESEND_SEGMENT_ID
   npx wrangler secret put SIGNING_SECRET
   ```

   `SIGNING_SECRET` should be a newly generated high-entropy value of at least 32 bytes.

4. Deploy with `npm run deploy`. The Worker claims `newsletter.behindthewatt.com` as its custom domain.
5. Submit a test address on the site, confirm it, and verify that it appears in the `BTW Weekly` Segment.

Worker secrets remain attached to the deployed Worker when GitHub Actions publishes a new version.

## Publishing an edition

1. Review and merge the Tuesday `digest/YYYY-MM-DD.md` PR.
2. Create a Resend Broadcast for the `BTW Weekly` Segment.
3. Use `Behind the Watt <weekly@updates.behindthewatt.com>` as the sender.
4. Include Resend's unsubscribe footer, send a test, inspect every evidence link, then schedule or send.

The human review gate is intentional. The engine drafts; it never sends a Broadcast on its own.

## Local verification

```sh
npm test
npm run type-check
```
