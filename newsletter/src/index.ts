interface Env {
  SITE_ORIGIN: string;
  RESEND_FROM: string;
  RESEND_API_KEY: string;
  RESEND_SEGMENT_ID: string;
  SIGNING_SECRET: string;
  DIGEST_TRIGGER_SECRET: string;
  DIGEST_STATE: KVNamespace;
  SUBSCRIBE_RATE_LIMITER: RateLimit;
}

type TokenPayload = { email: string; expires: number };
type DigestPayload = {
  issue_id: string;
  subject: string;
  preview_text: string;
  html: string;
  text: string;
  content_sha256: string;
};
type DigestState = {
  issue_id: string;
  content_sha256: string;
  broadcast_id: string;
  status: string;
  updated_at: string;
};

const encoder = new TextEncoder();
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function base64url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function fromBase64url(value: string): Uint8Array {
  const padded = value.replaceAll("-", "+").replaceAll("_", "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  const binary = atob(padded);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

async function signingKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}

export function normalizeEmail(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const email = value.trim().toLowerCase();
  return email.length <= 254 && EMAIL_PATTERN.test(email) ? email : null;
}

export async function signToken(payload: TokenPayload, secret: string): Promise<string> {
  const encoded = base64url(encoder.encode(JSON.stringify(payload)));
  const signature = await crypto.subtle.sign("HMAC", await signingKey(secret), encoder.encode(encoded));
  return `${encoded}.${base64url(new Uint8Array(signature))}`;
}

export async function verifyToken(token: string, secret: string, now = Date.now()): Promise<TokenPayload | null> {
  const [encoded, signature, extra] = token.split(".");
  if (!encoded || !signature || extra) return null;
  try {
    const signatureBytes = fromBase64url(signature);
    const valid = await crypto.subtle.verify("HMAC", await signingKey(secret), signatureBytes.buffer as ArrayBuffer, encoder.encode(encoded));
    if (!valid) return null;
    const payload = JSON.parse(new TextDecoder().decode(fromBase64url(encoded))) as TokenPayload;
    const email = normalizeEmail(payload.email);
    if (!email || !Number.isFinite(payload.expires) || payload.expires < now) return null;
    return { email, expires: payload.expires };
  } catch {
    return null;
  }
}

async function hash(value: string): Promise<string> {
  return base64url(new Uint8Array(await crypto.subtle.digest("SHA-256", encoder.encode(value))));
}

async function sha256Hex(value: string): Promise<string> {
  const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", encoder.encode(value)));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function digestContentHash(subject: string, html: string, text: string): Promise<string> {
  return sha256Hex(`${subject}\0${html}\0${text}`);
}

async function secretsMatch(provided: string, expected: string): Promise<boolean> {
  const [left, right] = await Promise.all([sha256Hex(provided), sha256Hex(expected)]);
  let different = left.length ^ right.length;
  for (let index = 0; index < Math.min(left.length, right.length); index += 1) {
    different |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return different === 0;
}

function responseHeaders(origin?: string): HeadersInit {
  return {
    "Access-Control-Allow-Origin": origin || "https://behindthewatt.com",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
  };
}

async function resend(env: Env, path: string, init: RequestInit): Promise<Response> {
  return fetch(`https://api.resend.com${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.RESEND_API_KEY}`,
      "Content-Type": "application/json",
      ...(init.headers || {}),
    },
  });
}

function confirmationEmail(confirmUrl: string, from: string, to: string) {
  return {
    from,
    to: [to],
    subject: "Confirm your BTW Weekly subscription",
    text: `Confirm your subscription to BTW Weekly:\n\n${confirmUrl}\n\nThis link expires in 24 hours. If you did not request this, ignore this email.`,
    html: `<!doctype html><html><body style="margin:0;background:#fbfaf7;color:#14140f;font-family:Arial,sans-serif"><div style="max-width:600px;margin:0 auto;padding:48px 24px"><p style="font-family:monospace;color:#8a897f;font-size:12px;text-transform:uppercase;letter-spacing:.08em">Behind the Watt</p><h1 style="font-family:Georgia,serif;font-size:34px;line-height:1.1">One click to join BTW Weekly</h1><p style="color:#57564e;line-height:1.6">Every Tuesday: new filings, verified megawatts, and what they mean.</p><p style="margin:30px 0"><a href="${confirmUrl}" style="display:inline-block;padding:12px 20px;border-radius:8px;background:#14140f;color:#fff;text-decoration:none;font-weight:600">Confirm subscription</a></p><p style="color:#8a897f;font-size:12px;line-height:1.5">This link expires in 24 hours. If you did not request this email, ignore it.</p></div></body></html>`,
    tags: [{ name: "category", value: "newsletter_confirmation" }],
  };
}

async function readForm(request: Request): Promise<Record<string, unknown>> {
  if (request.headers.get("content-type")?.includes("application/json")) {
    return await request.json() as Record<string, unknown>;
  }
  const data = await request.formData();
  return Object.fromEntries(data.entries());
}

function redirectStatus(request: Request, env: Env, status: "pending" | "error"): Response {
  if (request.headers.get("accept")?.includes("application/json")) {
    return Response.json(
      status === "pending" ? { ok: true, status } : { ok: false, status },
      { status: status === "pending" ? 202 : 502, headers: responseHeaders(env.SITE_ORIGIN) },
    );
  }
  return Response.redirect(`${env.SITE_ORIGIN}/?newsletter=${status}#subscribe`, 303);
}

async function subscribe(request: Request, env: Env): Promise<Response> {
  const origin = request.headers.get("origin");
  if (origin && origin !== env.SITE_ORIGIN) return new Response("Forbidden", { status: 403, headers: responseHeaders(env.SITE_ORIGIN) });

  let form: Record<string, unknown>;
  try {
    form = await readForm(request);
  } catch {
    return Response.json({ ok: false, error: "invalid_request" }, { status: 400, headers: responseHeaders(env.SITE_ORIGIN) });
  }
  if (form.company) return redirectStatus(request, env, "pending");
  const email = normalizeEmail(form.email);
  if (!email) return Response.json({ ok: false, error: "invalid_email" }, { status: 400, headers: responseHeaders(env.SITE_ORIGIN) });

  const rate = await env.SUBSCRIBE_RATE_LIMITER.limit({ key: await hash(email) });
  if (!rate.success) return Response.json({ ok: false, error: "rate_limited" }, { status: 429, headers: responseHeaders(env.SITE_ORIGIN) });

  const token = await signToken({ email, expires: Date.now() + 24 * 60 * 60 * 1000 }, env.SIGNING_SECRET);
  const confirmUrl = `https://newsletter.behindthewatt.com/confirm?token=${encodeURIComponent(token)}`;
  const sent = await resend(env, "/emails", {
    method: "POST",
    headers: { "Idempotency-Key": `btw-confirm-${(await hash(`${email}:${new Date().toISOString().slice(0, 10)}`)).slice(0, 40)}` },
    body: JSON.stringify(confirmationEmail(confirmUrl, env.RESEND_FROM, email)),
  });
  if (!sent.ok) {
    console.error("confirmation_send_failed", sent.status, (await sent.text()).slice(0, 300));
    return redirectStatus(request, env, "error");
  }
  return redirectStatus(request, env, "pending");
}

async function confirm(request: Request, env: Env): Promise<Response> {
  const token = new URL(request.url).searchParams.get("token") || "";
  const payload = await verifyToken(token, env.SIGNING_SECRET);
  if (!payload) return Response.redirect(`${env.SITE_ORIGIN}/newsletter/invalid/`, 303);

  const created = await resend(env, "/contacts", {
    method: "POST",
    body: JSON.stringify({
      email: payload.email,
      unsubscribed: false,
      segments: [{ id: env.RESEND_SEGMENT_ID }],
    }),
  });
  if (created.status === 409) {
    const contact = encodeURIComponent(payload.email);
    const updated = await resend(env, `/contacts/${contact}`, { method: "PATCH", body: JSON.stringify({ unsubscribed: false }) });
    const segmented = await resend(env, `/contacts/${contact}/segments/${encodeURIComponent(env.RESEND_SEGMENT_ID)}`, { method: "POST" });
    if (!updated.ok || (!segmented.ok && segmented.status !== 409)) {
      console.error("contact_update_failed", updated.status, segmented.status);
      return Response.redirect(`${env.SITE_ORIGIN}/newsletter/invalid/`, 303);
    }
  } else if (!created.ok) {
    console.error("contact_create_failed", created.status, (await created.text()).slice(0, 300));
    return Response.redirect(`${env.SITE_ORIGIN}/newsletter/invalid/`, 303);
  }
  return Response.redirect(`${env.SITE_ORIGIN}/newsletter/confirmed/`, 303);
}

function serverHeaders(): HeadersInit {
  return {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
  };
}

async function authorizeDigest(request: Request, env: Env): Promise<boolean> {
  const authorization = request.headers.get("authorization") || "";
  if (!authorization.startsWith("Bearer ")) return false;
  return secretsMatch(authorization.slice(7), env.DIGEST_TRIGGER_SECRET);
}

function validIssueId(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}

function validDigestPayload(value: unknown): value is DigestPayload {
  if (!value || typeof value !== "object") return false;
  const payload = value as Record<string, unknown>;
  return validIssueId(payload.issue_id)
    && typeof payload.subject === "string" && payload.subject.length > 0 && payload.subject.length <= 200
    && typeof payload.preview_text === "string" && payload.preview_text.length <= 300
    && typeof payload.html === "string" && payload.html.length > 0 && payload.html.length <= 180_000
    && typeof payload.text === "string" && payload.text.length > 0 && payload.text.length <= 120_000
    && payload.html.includes("{{{RESEND_UNSUBSCRIBE_URL}}}")
    && payload.text.includes("{{{RESEND_UNSUBSCRIBE_URL}}}")
    && typeof payload.content_sha256 === "string" && /^[0-9a-f]{64}$/.test(payload.content_sha256);
}

async function resendJson(response: Response): Promise<Record<string, any>> {
  try {
    return await response.json() as Record<string, any>;
  } catch {
    return {};
  }
}

async function getBroadcast(env: Env, id: string): Promise<Record<string, any> | null> {
  const response = await resend(env, `/broadcasts/${encodeURIComponent(id)}`, { method: "GET" });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`broadcast_lookup_failed:${response.status}`);
  return resendJson(response);
}

async function findBroadcastByName(env: Env, name: string): Promise<Record<string, any> | null> {
  const response = await resend(env, "/broadcasts", { method: "GET" });
  if (!response.ok) throw new Error(`broadcast_list_failed:${response.status}`);
  const listed = await resendJson(response);
  const recent = Array.isArray(listed.data) ? listed.data.slice(0, 25) : [];
  for (const item of recent) {
    if (!item?.id) continue;
    const detail = await getBroadcast(env, String(item.id));
    if (detail?.name === name) return detail;
  }
  return null;
}

async function saveDigestState(env: Env, state: DigestState): Promise<void> {
  await env.DIGEST_STATE.put(`edition:${state.issue_id}`, JSON.stringify(state));
}

async function requestBroadcastSend(env: Env, state: DigestState): Promise<DigestState> {
  const current = await getBroadcast(env, state.broadcast_id);
  if (!current) throw new Error("broadcast_missing_after_create");
  if (current.status !== "draft") {
    const settled = { ...state, status: String(current.status || "accepted"), updated_at: new Date().toISOString() };
    await saveDigestState(env, settled);
    return settled;
  }

  const response = await resend(env, `/broadcasts/${encodeURIComponent(state.broadcast_id)}/send`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  if (!response.ok) {
    const afterFailure = await getBroadcast(env, state.broadcast_id);
    if (!afterFailure || afterFailure.status === "draft") {
      throw new Error(`broadcast_send_failed:${response.status}`);
    }
  }
  const sent = { ...state, status: "send_requested", updated_at: new Date().toISOString() };
  await saveDigestState(env, sent);
  return sent;
}

async function validateBroadcastDelivery(env: Env): Promise<Response> {
  const segment = await resend(env, `/segments/${encodeURIComponent(env.RESEND_SEGMENT_ID)}`, { method: "GET" });
  if (!segment.ok) {
    console.error("digest_segment_validation_failed", segment.status, (await segment.text()).slice(0, 300));
    return Response.json({ ok: false, error: "delivery_not_ready" }, { status: 502, headers: serverHeaders() });
  }
  const broadcasts = await resend(env, "/broadcasts", { method: "GET" });
  if (!broadcasts.ok) {
    console.error("digest_broadcast_validation_failed", broadcasts.status, (await broadcasts.text()).slice(0, 300));
    return Response.json({ ok: false, error: "delivery_not_ready" }, { status: 502, headers: serverHeaders() });
  }
  await env.DIGEST_STATE.get("delivery-healthcheck");
  return Response.json({ ok: true, status: "ready" }, { headers: serverHeaders() });
}

async function broadcast(request: Request, env: Env): Promise<Response> {
  if (!await authorizeDigest(request, env)) {
    return Response.json({ ok: false, error: "unauthorized" }, { status: 401, headers: serverHeaders() });
  }
  const length = Number(request.headers.get("content-length") || "0");
  if (length > 310_000) {
    return Response.json({ ok: false, error: "payload_too_large" }, { status: 413, headers: serverHeaders() });
  }

  let value: unknown;
  try {
    value = await request.json();
  } catch {
    return Response.json({ ok: false, error: "invalid_json" }, { status: 400, headers: serverHeaders() });
  }
  if ((value as Record<string, unknown> | null)?.mode === "validate") {
    return validateBroadcastDelivery(env);
  }
  if (!validDigestPayload(value)) {
    return Response.json({ ok: false, error: "invalid_digest" }, { status: 400, headers: serverHeaders() });
  }
  const payload = value as DigestPayload;
  const actualHash = await digestContentHash(payload.subject, payload.html, payload.text);
  if (actualHash !== payload.content_sha256) {
    return Response.json({ ok: false, error: "content_hash_mismatch" }, { status: 400, headers: serverHeaders() });
  }

  try {
    const key = `edition:${payload.issue_id}`;
    const stored = await env.DIGEST_STATE.get<DigestState>(key, "json");
    if (stored) {
      if (stored.content_sha256 !== payload.content_sha256) {
        return Response.json({ ok: false, error: "edition_content_conflict" }, { status: 409, headers: serverHeaders() });
      }
      const state = await requestBroadcastSend(env, stored);
      return Response.json({ ok: true, duplicate: true, broadcast_id: state.broadcast_id, status: state.status }, { headers: serverHeaders() });
    }

    const name = `BTW Weekly ${payload.issue_id}`;
    const recovered = await findBroadcastByName(env, name);
    let state: DigestState;
    if (recovered?.id) {
      state = {
        issue_id: payload.issue_id,
        content_sha256: payload.content_sha256,
        broadcast_id: String(recovered.id),
        status: String(recovered.status || "recovered"),
        updated_at: new Date().toISOString(),
      };
      await saveDigestState(env, state);
    } else {
      const created = await resend(env, "/broadcasts", {
        method: "POST",
        body: JSON.stringify({
          segment_id: env.RESEND_SEGMENT_ID,
          from: env.RESEND_FROM,
          name,
          subject: payload.subject,
          preview_text: payload.preview_text,
          html: payload.html,
          text: payload.text,
        }),
      });
      const createdBody = await resendJson(created);
      if (!created.ok || !createdBody.id) {
        console.error("broadcast_create_failed", created.status, JSON.stringify(createdBody).slice(0, 300));
        return Response.json({ ok: false, error: "broadcast_create_failed" }, { status: 502, headers: serverHeaders() });
      }
      state = {
        issue_id: payload.issue_id,
        content_sha256: payload.content_sha256,
        broadcast_id: String(createdBody.id),
        status: "draft",
        updated_at: new Date().toISOString(),
      };
      // Persist the ID before asking Resend to send. A retry can inspect the
      // draft or sent status instead of creating a second broadcast.
      await saveDigestState(env, state);
    }

    state = await requestBroadcastSend(env, state);
    return Response.json({ ok: true, duplicate: Boolean(recovered), broadcast_id: state.broadcast_id, status: state.status }, { status: 202, headers: serverHeaders() });
  } catch (error) {
    console.error("broadcast_delivery_failed", error instanceof Error ? error.message : String(error));
    return Response.json({ ok: false, error: "broadcast_delivery_failed" }, { status: 502, headers: serverHeaders() });
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "OPTIONS" && url.pathname === "/subscribe") {
      const origin = request.headers.get("origin");
      if (origin && origin !== env.SITE_ORIGIN) return new Response(null, { status: 403 });
      return new Response(null, { status: 204, headers: responseHeaders(env.SITE_ORIGIN) });
    }
    if (request.method === "POST" && url.pathname === "/subscribe") return subscribe(request, env);
    if (request.method === "POST" && url.pathname === "/broadcast") return broadcast(request, env);
    if (request.method === "GET" && url.pathname === "/confirm") return confirm(request, env);
    if (request.method === "GET" && url.pathname === "/health") return Response.json({ ok: true, service: "behind-the-watt-newsletter" });
    if (url.pathname === "/") return Response.redirect(`${env.SITE_ORIGIN}/#subscribe`, 302);
    return new Response("Not found", { status: 404, headers: responseHeaders(env.SITE_ORIGIN) });
  },
} satisfies ExportedHandler<Env>;
