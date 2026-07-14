import assert from "node:assert/strict";
import test from "node:test";
import worker, { normalizeEmail, signToken, verifyToken } from "../src/index.ts";

const env = {
  SITE_ORIGIN: "https://behindthewatt.com",
  RESEND_FROM: "Behind the Watt <weekly@updates.behindthewatt.com>",
  RESEND_API_KEY: "test-key",
  RESEND_SEGMENT_ID: "segment-test",
  SIGNING_SECRET: "test-secret-that-is-long-enough",
  SUBSCRIBE_RATE_LIMITER: { limit: async () => ({ success: true }) },
};

test("normalizes valid email and rejects malformed input", () => {
  assert.equal(normalizeEmail("  Editor@Example.COM "), "editor@example.com");
  assert.equal(normalizeEmail("not-an-email"), null);
  assert.equal(normalizeEmail(null), null);
});

test("round trips a signed confirmation token", async () => {
  const secret = "test-secret-that-is-long-enough";
  const payload = { email: "reader@example.com", expires: Date.now() + 60_000 };
  const token = await signToken(payload, secret);
  assert.deepEqual(await verifyToken(token, secret), payload);
});

test("rejects expired and tampered confirmation tokens", async () => {
  const secret = "test-secret-that-is-long-enough";
  const expired = await signToken({ email: "reader@example.com", expires: 1 }, secret);
  assert.equal(await verifyToken(expired, secret), null);
  assert.equal(await verifyToken(`${expired}x`, secret), null);
});

test("subscription sends confirmation without creating a contact", { concurrency: false }, async () => {
  const originalFetch = globalThis.fetch;
  let outbound: any = null;
  globalThis.fetch = async (_input, init) => {
    outbound = JSON.parse(String(init?.body));
    return new Response(JSON.stringify({ id: "email-test" }), { status: 200 });
  };
  try {
    const body = new FormData();
    body.set("email", "reader@example.com");
    const response = await worker.fetch(new Request("https://newsletter.behindthewatt.com/subscribe", {
      method: "POST",
      headers: { Origin: env.SITE_ORIGIN, Accept: "application/json" },
      body,
    }), env as never);
    assert.equal(response.status, 202);
    assert.equal(((await response.json()) as { status: string }).status, "pending");
    assert.deepEqual(outbound?.to, ["reader@example.com"]);
    assert.equal(outbound && "segments" in outbound, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("confirmation creates the Resend contact and segment membership", { concurrency: false }, async () => {
  const originalFetch = globalThis.fetch;
  let outbound: any = null;
  globalThis.fetch = async (_input, init) => {
    outbound = JSON.parse(String(init?.body));
    return new Response(JSON.stringify({ id: "contact-test" }), { status: 200 });
  };
  try {
    const token = await signToken({ email: "reader@example.com", expires: Date.now() + 60_000 }, env.SIGNING_SECRET);
    const response = await worker.fetch(new Request(`https://newsletter.behindthewatt.com/confirm?token=${encodeURIComponent(token)}`), env as never);
    assert.equal(response.status, 303);
    assert.equal(response.headers.get("location"), "https://behindthewatt.com/newsletter/confirmed/");
    assert.deepEqual(outbound?.segments, [{ id: "segment-test" }]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
