import assert from "node:assert/strict";
import test from "node:test";
import worker, { digestContentHash, normalizeEmail, signToken, verifyToken } from "../src/index.ts";

class FakeKV {
  values = new Map<string, string>();

  async get<T>(key: string, type?: string): Promise<T | string | null> {
    const value = this.values.get(key);
    if (value === undefined) return null;
    return type === "json" ? JSON.parse(value) as T : value;
  }

  async put(key: string, value: string): Promise<void> {
    this.values.set(key, value);
  }
}

const env = {
  SITE_ORIGIN: "https://behindthewatt.com",
  RESEND_FROM: "Behind the Watt <weekly@updates.behindthewatt.com>",
  RESEND_API_KEY: "test-key",
  RESEND_SEGMENT_ID: "segment-test",
  SIGNING_SECRET: "test-secret-that-is-long-enough",
  DIGEST_TRIGGER_SECRET: "digest-trigger-test-secret",
  DIGEST_STATE: new FakeKV(),
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

async function digestPayload(issue = "2026-07-21") {
  const subject = `BTW Weekly — ${issue}: no new published record changes`;
  const html = "<p>Published record.</p><a href=\"{{{RESEND_UNSUBSCRIBE_URL}}}\">Unsubscribe</a>";
  const text = "Published record.\n\nUnsubscribe: {{{RESEND_UNSUBSCRIBE_URL}}}";
  return {
    issue_id: issue,
    subject,
    preview_text: "Verified operating record 0.70 GW.",
    html,
    text,
    content_sha256: await digestContentHash(subject, html, text),
  };
}

test("broadcast endpoint requires its server-side bearer secret", async () => {
  const response = await worker.fetch(new Request("https://newsletter.behindthewatt.com/broadcast", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(await digestPayload()),
  }), { ...env, DIGEST_STATE: new FakeKV() } as never);
  assert.equal(response.status, 401);
});

test("delivery validation checks the configured Resend segment without sending", { concurrency: false }, async () => {
  const originalFetch = globalThis.fetch;
  const calls: string[] = [];
  globalThis.fetch = async (input) => {
    calls.push(String(input));
    return new Response(JSON.stringify({ id: "segment-test", name: "BTW Weekly" }), { status: 200 });
  };
  try {
    const response = await worker.fetch(new Request("https://newsletter.behindthewatt.com/broadcast", {
      method: "POST",
      headers: { Authorization: `Bearer ${env.DIGEST_TRIGGER_SECRET}`, "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "validate" }),
    }), { ...env, DIGEST_STATE: new FakeKV() } as never);
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { ok: true, status: "ready" });
    assert.deepEqual(calls, [
      "https://api.resend.com/segments/segment-test",
      "https://api.resend.com/broadcasts",
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("broadcast delivery persists the draft before send and is idempotent on retry", { concurrency: false }, async () => {
  const originalFetch = globalThis.fetch;
  const kv = new FakeKV();
  const calls: Array<{ url: string; method: string; body: any }> = [];
  let status = "draft";
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    const method = init?.method || "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    if (url.endsWith("/broadcasts") && method === "GET") {
      return new Response(JSON.stringify({ data: [] }), { status: 200 });
    }
    if (url.endsWith("/broadcasts") && method === "POST") {
      return new Response(JSON.stringify({ id: "broadcast-test" }), { status: 200 });
    }
    if (url.endsWith("/broadcasts/broadcast-test") && method === "GET") {
      return new Response(JSON.stringify({ id: "broadcast-test", status }), { status: 200 });
    }
    if (url.endsWith("/broadcasts/broadcast-test/send") && method === "POST") {
      status = "sent";
      return new Response(JSON.stringify({ id: "broadcast-test" }), { status: 200 });
    }
    return new Response("unexpected", { status: 500 });
  };

  try {
    const payload = await digestPayload();
    const request = () => new Request("https://newsletter.behindthewatt.com/broadcast", {
      method: "POST",
      headers: { Authorization: `Bearer ${env.DIGEST_TRIGGER_SECRET}`, "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const first = await worker.fetch(request(), { ...env, DIGEST_STATE: kv } as never);
    assert.equal(first.status, 202);
    assert.equal((await first.json() as any).broadcast_id, "broadcast-test");
    const stored = JSON.parse(kv.values.get("edition:2026-07-21") || "null");
    assert.equal(stored.broadcast_id, "broadcast-test");
    assert.equal(stored.status, "send_requested");

    const created = calls.find((call) => call.url.endsWith("/broadcasts") && call.method === "POST");
    assert.equal(created?.body.segment_id, "segment-test");
    assert.equal(created?.body.name, "BTW Weekly 2026-07-21");
    assert.equal(created?.body.send, undefined);
    assert.ok(calls.some((call) => call.url.endsWith("/broadcasts/broadcast-test/send")));

    const createCount = calls.filter((call) => call.url.endsWith("/broadcasts") && call.method === "POST").length;
    const second = await worker.fetch(request(), { ...env, DIGEST_STATE: kv } as never);
    assert.equal(second.status, 200);
    assert.equal((await second.json() as any).duplicate, true);
    assert.equal(calls.filter((call) => call.url.endsWith("/broadcasts") && call.method === "POST").length, createCount);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("same issue id with different content fails closed", { concurrency: false }, async () => {
  const kv = new FakeKV();
  const original = await digestPayload();
  kv.values.set("edition:2026-07-21", JSON.stringify({
    issue_id: original.issue_id,
    content_sha256: original.content_sha256,
    broadcast_id: "broadcast-test",
    status: "sent",
    updated_at: new Date().toISOString(),
  }));
  const changed = await digestPayload();
  changed.subject += " changed";
  changed.content_sha256 = await digestContentHash(changed.subject, changed.html, changed.text);

  const response = await worker.fetch(new Request("https://newsletter.behindthewatt.com/broadcast", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.DIGEST_TRIGGER_SECRET}`, "Content-Type": "application/json" },
    body: JSON.stringify(changed),
  }), { ...env, DIGEST_STATE: kv } as never);
  assert.equal(response.status, 409);
  assert.deepEqual(await response.json(), { ok: false, error: "edition_content_conflict" });
});
