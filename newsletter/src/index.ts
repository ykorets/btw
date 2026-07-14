interface Env {
  SITE_ORIGIN: string;
  RESEND_FROM: string;
  RESEND_API_KEY: string;
  RESEND_SEGMENT_ID: string;
  EDITOR_EMAIL: string;
  SIGNING_SECRET: string;
  DIGEST_TRIGGER_SECRET: string;
  DIGEST_STATE: KVNamespace;
  SUBSCRIBE_RATE_LIMITER: RateLimit;
}

type TokenPayload = { email: string; expires: number };
type ReviewTokenPayload = {
  kind: "digest_review";
  issue_id: string;
  broadcast_id: string;
  expires: number;
};
type DigestPayload = {
  mode: "review";
  issue_id: string;
  window_start: string;
  window_end: string;
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
  window_start: string;
  window_end: string;
  review_expires: number;
  preview_email_id?: string;
  approved_at?: string;
  scheduled_for?: string;
  approved_content_sha256?: string;
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

async function signPayload(payload: Record<string, unknown>, secret: string): Promise<string> {
  const encoded = base64url(encoder.encode(JSON.stringify(payload)));
  const signature = await crypto.subtle.sign("HMAC", await signingKey(secret), encoder.encode(encoded));
  return `${encoded}.${base64url(new Uint8Array(signature))}`;
}

async function verifyPayload(token: string, secret: string): Promise<Record<string, unknown> | null> {
  const [encoded, signature, extra] = token.split(".");
  if (!encoded || !signature || extra) return null;
  try {
    const signatureBytes = fromBase64url(signature);
    const valid = await crypto.subtle.verify(
      "HMAC",
      await signingKey(secret),
      signatureBytes.buffer as ArrayBuffer,
      encoder.encode(encoded),
    );
    if (!valid) return null;
    const decoded = JSON.parse(new TextDecoder().decode(fromBase64url(encoded)));
    return decoded && typeof decoded === "object" ? decoded as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

export async function signReviewToken(payload: ReviewTokenPayload, secret: string): Promise<string> {
  return signPayload(payload, secret);
}

export async function verifyReviewToken(
  token: string,
  secret: string,
  now = Date.now(),
): Promise<ReviewTokenPayload | null> {
  const payload = await verifyPayload(token, secret);
  if (!payload || payload.kind !== "digest_review" || !validIssueId(payload.issue_id)
    || typeof payload.broadcast_id !== "string" || !payload.broadcast_id
    || !Number.isFinite(payload.expires) || Number(payload.expires) < now) return null;
  return {
    kind: "digest_review",
    issue_id: payload.issue_id,
    broadcast_id: payload.broadcast_id,
    expires: Number(payload.expires),
  };
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
  return payload.mode === "review"
    && validIssueId(payload.issue_id)
    && typeof payload.window_start === "string" && Number.isFinite(Date.parse(payload.window_start))
    && typeof payload.window_end === "string" && Number.isFinite(Date.parse(payload.window_end))
    && Date.parse(payload.window_start) < Date.parse(payload.window_end)
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

function reviewExpiry(issueId: string): number {
  return Date.parse(`${issueId}T13:00:00Z`) + 2 * 24 * 60 * 60 * 1000;
}

function scheduleForIssue(issueId: string): string {
  return `${issueId}T13:00:00.000Z`;
}

function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function reviewUrl(requestUrl: string, token: string): string {
  const url = new URL(requestUrl);
  url.pathname = "/review";
  url.search = `?token=${encodeURIComponent(token)}`;
  return url.toString();
}

function resendEditUrl(broadcastId: string): string {
  return `https://resend.com/broadcasts/${encodeURIComponent(broadcastId)}`;
}

function injectEditorBanner(html: string, reviewLink: string, editLink: string): string {
  const banner = `
<div style="background:#14140f;color:#fbfaf7;padding:22px 18px;font-family:Arial,sans-serif">
  <div style="max-width:680px;margin:0 auto">
    <p style="margin:0 0 7px;color:#b9dcca;font-size:11px;font-weight:700;letter-spacing:.12em;text-transform:uppercase">Editor preview · not sent to subscribers</p>
    <p style="margin:0 0 15px;font-family:Georgia,serif;font-size:23px;line-height:1.25">Review the Tuesday edition</p>
    <a href="${escapeHtml(reviewLink)}" style="display:inline-block;margin:0 8px 8px 0;padding:11px 16px;border-radius:7px;background:#e4f3ec;color:#0f5f40;text-decoration:none;font-weight:700">Review &amp; approve</a>
    <a href="${escapeHtml(editLink)}" style="display:inline-block;margin:0 0 8px;padding:10px 15px;border:1px solid #57564e;border-radius:7px;color:#fbfaf7;text-decoration:none;font-weight:600">Edit in Resend</a>
    <p style="margin:5px 0 0;color:#b9b8b0;font-size:12px;line-height:1.5">Opening this email cannot send the edition. Delivery requires a separate confirmation on the review page.</p>
  </div>
</div>`;
  const withSafeUnsubscribe = html.replaceAll("{{{RESEND_UNSUBSCRIBE_URL}}}", "https://behindthewatt.com/#subscribe");
  return /<body\b[^>]*>/i.test(withSafeUnsubscribe)
    ? withSafeUnsubscribe.replace(/(<body\b[^>]*>)/i, `$1${banner}`)
    : banner + withSafeUnsubscribe;
}

async function reviewTokenFor(env: Env, state: DigestState): Promise<string> {
  return signReviewToken({
    kind: "digest_review",
    issue_id: state.issue_id,
    broadcast_id: state.broadcast_id,
    expires: state.review_expires,
  }, env.SIGNING_SECRET);
}

async function sendEditorPreview(
  request: Request,
  env: Env,
  payload: DigestPayload,
  state: DigestState,
): Promise<DigestState> {
  const editor = normalizeEmail(env.EDITOR_EMAIL);
  if (!editor) throw new Error("editor_email_invalid");
  const token = await reviewTokenFor(env, state);
  const reviewLink = reviewUrl(request.url, token);
  const editLink = resendEditUrl(state.broadcast_id);
  const response = await resend(env, "/emails", {
    method: "POST",
    headers: { "Idempotency-Key": `btw-review-${state.issue_id}-${payload.content_sha256.slice(0, 20)}` },
    body: JSON.stringify({
      from: env.RESEND_FROM,
      to: [editor],
      subject: `[Review required] ${payload.subject}`,
      text: [
        `EDITOR PREVIEW — this edition has not been sent to subscribers.`,
        `Review and approve: ${reviewLink}`,
        `Edit in Resend: ${editLink}`,
        "",
        payload.text.replaceAll("{{{RESEND_UNSUBSCRIBE_URL}}}", `${env.SITE_ORIGIN}/#subscribe`),
      ].join("\n"),
      html: injectEditorBanner(payload.html, reviewLink, editLink),
      tags: [
        { name: "category", value: "newsletter_editor_review" },
        { name: "issue", value: state.issue_id.replaceAll("-", "_") },
      ],
    }),
  });
  const body = await resendJson(response);
  if (!response.ok || !body.id) throw new Error(`editor_preview_failed:${response.status}`);
  const awaiting = {
    ...state,
    status: "awaiting_approval",
    preview_email_id: String(body.id),
    updated_at: new Date().toISOString(),
  };
  await saveDigestState(env, awaiting);
  return awaiting;
}

function acceptedStatus(status: string): boolean {
  return ["scheduled", "send_requested", "queued", "sent", "delivered"].includes(status);
}

async function deliveryCursor(env: Env): Promise<Response> {
  let cursor: string | undefined;
  let latest: DigestState | null = null;
  let latestWindowEnd: string | null = null;
  do {
    const page = await env.DIGEST_STATE.list({ prefix: "edition:", cursor });
    for (const key of page.keys) {
      const state = await env.DIGEST_STATE.get<DigestState>(key.name, "json");
      if (!state || !acceptedStatus(state.status)) continue;
      // Editions delivered by the pre-review workflow ended on the Tuesday
      // issue date and do not have explicit window fields in KV.
      const candidate = typeof state.window_end === "string" && Number.isFinite(Date.parse(state.window_end))
        ? state.window_end
        : validIssueId(state.issue_id) ? `${state.issue_id}T13:00:00Z` : null;
      if (!candidate) continue;
      if (!latestWindowEnd || Date.parse(candidate) > Date.parse(latestWindowEnd)) {
        latest = state;
        latestWindowEnd = candidate;
      }
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return Response.json({
    ok: true,
    window_end: latestWindowEnd,
    issue_id: latest?.issue_id ?? null,
    status: latest?.status ?? null,
  }, { headers: serverHeaders() });
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
  if ((value as Record<string, unknown> | null)?.mode === "cursor") {
    return deliveryCursor(env);
  }
  if (!validDigestPayload(value)) {
    return Response.json({ ok: false, error: "invalid_digest" }, { status: 400, headers: serverHeaders() });
  }
  const payload = value as DigestPayload;
  const actualHash = await digestContentHash(payload.subject, payload.html, payload.text);
  if (actualHash !== payload.content_sha256) {
    return Response.json({ ok: false, error: "content_hash_mismatch" }, { status: 400, headers: serverHeaders() });
  }
  const expires = reviewExpiry(payload.issue_id);
  if (expires <= Date.now()) {
    return Response.json({ ok: false, error: "review_window_expired" }, { status: 409, headers: serverHeaders() });
  }

  try {
    const key = `edition:${payload.issue_id}`;
    const stored = await env.DIGEST_STATE.get<DigestState>(key, "json");
    if (stored) {
      if (stored.content_sha256 !== payload.content_sha256) {
        return Response.json({ ok: false, error: "edition_content_conflict" }, { status: 409, headers: serverHeaders() });
      }
      let state = stored;
      if (["draft_created", "preview_failed"].includes(stored.status)) {
        state = await sendEditorPreview(request, env, payload, stored);
      }
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
        status: String(recovered.status || "draft_created") === "draft" ? "draft_created" : String(recovered.status || "recovered"),
        window_start: payload.window_start,
        window_end: payload.window_end,
        review_expires: expires,
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
        status: "draft_created",
        window_start: payload.window_start,
        window_end: payload.window_end,
        review_expires: expires,
        updated_at: new Date().toISOString(),
      };
      // Persist the ID before asking Resend to send. A retry can inspect the
      // draft or sent status instead of creating a second broadcast.
      await saveDigestState(env, state);
    }

    if (state.status === "draft_created") {
      try {
        state = await sendEditorPreview(request, env, payload, state);
      } catch (error) {
        const failed = { ...state, status: "preview_failed", updated_at: new Date().toISOString() };
        await saveDigestState(env, failed);
        throw error;
      }
    }
    return Response.json({ ok: true, duplicate: Boolean(recovered), broadcast_id: state.broadcast_id, status: state.status }, { status: 202, headers: serverHeaders() });
  } catch (error) {
    console.error("broadcast_review_failed", error instanceof Error ? error.message : String(error));
    return Response.json({ ok: false, error: "broadcast_review_failed" }, { status: 502, headers: serverHeaders() });
  }
}

function reviewHeaders(): HeadersInit {
  return {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src https: data:; frame-src 'self' data:; form-action 'self'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
  };
}

function reviewShell(title: string, body: string, status = 200): Response {
  return new Response(`<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escapeHtml(title)}</title></head><body style="margin:0;background:#fbfaf7;color:#14140f;font-family:Arial,sans-serif"><main style="max-width:920px;margin:0 auto;padding:40px 20px 70px"><div style="margin-bottom:28px;font-family:Georgia,serif;font-size:20px">behind <span style="color:#157a54">|</span> the watt</div>${body}</main></body></html>`, {
    status,
    headers: { ...reviewHeaders(), "Content-Type": "text/html; charset=utf-8" },
  });
}

function invalidReview(): Response {
  return reviewShell("Review link unavailable", `<div style="max-width:620px;padding:28px;border:1px solid #e6e4dc;border-radius:12px;background:#fff"><p style="margin:0 0 8px;color:#9a6b10;font-size:12px;font-weight:700;letter-spacing:.1em;text-transform:uppercase">No delivery occurred</p><h1 style="margin:0 0 12px;font-family:Georgia,serif;font-size:36px;font-weight:500">This review link is invalid or expired.</h1><p style="margin:0;color:#57564e;line-height:1.6">Use the newest Monday preview email. An expired link can never send a newsletter.</p></div>`, 403);
}

async function review(request: Request, env: Env): Promise<Response> {
  const token = new URL(request.url).searchParams.get("token") || "";
  const payload = await verifyReviewToken(token, env.SIGNING_SECRET);
  if (!payload) return invalidReview();
  const state = await env.DIGEST_STATE.get<DigestState>(`edition:${payload.issue_id}`, "json");
  if (!state || state.broadcast_id !== payload.broadcast_id || state.review_expires !== payload.expires) return invalidReview();

  let current: Record<string, any> | null;
  try {
    current = await getBroadcast(env, state.broadcast_id);
  } catch {
    return reviewShell("Review temporarily unavailable", `<h1 style="font-family:Georgia,serif;font-weight:500">The draft could not be loaded.</h1><p style="color:#57564e">Nothing was sent. Please try this link again.</p>`, 502);
  }
  if (!current) return invalidReview();

  const status = String(current.status || state.status);
  const editable = status === "draft" && !acceptedStatus(state.status);
  const subject = String(current.subject || `BTW Weekly — ${state.issue_id}`);
  const currentHtml = typeof current.html === "string" ? current.html : "<p>Open the Resend draft to inspect its current contents.</p>";
  const action = editable ? `
    <form method="post" action="/broadcast/approve" style="display:inline-block;margin:0 8px 8px 0">
      <input type="hidden" name="token" value="${escapeHtml(token)}">
      <button type="submit" style="appearance:none;border:0;border-radius:8px;background:#157a54;color:#fff;padding:13px 18px;font:700 14px Arial,sans-serif;cursor:pointer">Approve Tuesday delivery</button>
    </form>
    <a href="${escapeHtml(resendEditUrl(state.broadcast_id))}" style="display:inline-block;margin:0 0 8px;padding:12px 17px;border:1px solid #d4d2c7;border-radius:8px;color:#14140f;text-decoration:none;font-weight:700;font-size:14px">Edit in Resend</a>` : `
    <p style="display:inline-block;margin:0;padding:12px 16px;border-radius:8px;background:#e4f3ec;color:#0f5f40;font-weight:700">Delivery status: ${escapeHtml(status)}</p>`;
  return reviewShell(`Review ${state.issue_id}`, `
    <section style="margin-bottom:24px;padding:26px;border:1px solid #e6e4dc;border-radius:12px;background:#fff">
      <p style="margin:0 0 8px;color:#157a54;font-size:11px;font-weight:700;letter-spacing:.12em;text-transform:uppercase">Editor gate · opening this page never sends</p>
      <h1 style="margin:0 0 10px;font-family:Georgia,serif;font-size:38px;line-height:1.12;font-weight:500">${escapeHtml(subject)}</h1>
      <p style="margin:0 0 20px;color:#57564e;line-height:1.55">Review the current Resend draft below. If you edit it, save there, return here and refresh before approving. Approval schedules it for Tuesday at 13:00 UTC; after that time, it sends immediately.</p>
      ${action}
    </section>
    <p style="margin:0 0 8px;color:#8a897f;font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase">Current subscriber version</p>
    <iframe sandbox title="Newsletter preview" srcdoc="${escapeHtml(currentHtml)}" style="display:block;width:100%;height:900px;border:1px solid #d4d2c7;border-radius:12px;background:#fff"></iframe>
  `);
}

async function approvedContentHash(current: Record<string, any>): Promise<string | undefined> {
  if (typeof current.subject !== "string" || typeof current.html !== "string" || typeof current.text !== "string") return undefined;
  return digestContentHash(current.subject, current.html, current.text);
}

async function approve(request: Request, env: Env): Promise<Response> {
  let form: Record<string, unknown>;
  try {
    form = await readForm(request);
  } catch {
    return invalidReview();
  }
  const token = typeof form.token === "string" ? form.token : "";
  const payload = await verifyReviewToken(token, env.SIGNING_SECRET);
  if (!payload) return invalidReview();
  const state = await env.DIGEST_STATE.get<DigestState>(`edition:${payload.issue_id}`, "json");
  if (!state || state.broadcast_id !== payload.broadcast_id || state.review_expires !== payload.expires) return invalidReview();

  try {
    const current = await getBroadcast(env, state.broadcast_id);
    if (!current) return invalidReview();
    const currentStatus = String(current.status || "unknown");
    if (currentStatus !== "draft") {
      const settled = { ...state, status: currentStatus, updated_at: new Date().toISOString() };
      await saveDigestState(env, settled);
      return reviewShell("Edition already approved", `<p style="color:#157a54;font-weight:700">No duplicate request was made.</p><h1 style="font-family:Georgia,serif;font-size:38px;font-weight:500">This edition is already ${escapeHtml(currentStatus)}.</h1>`);
    }

    const scheduledFor = scheduleForIssue(state.issue_id);
    const scheduleInFuture = Date.parse(scheduledFor) > Date.now();
    const sendBody = scheduleInFuture ? { scheduled_at: scheduledFor } : {};
    const response = await resend(env, `/broadcasts/${encodeURIComponent(state.broadcast_id)}/send`, {
      method: "POST",
      headers: { "Idempotency-Key": `btw-approve-${state.issue_id}-${state.broadcast_id}` },
      body: JSON.stringify(sendBody),
    });
    if (!response.ok) {
      const afterFailure = await getBroadcast(env, state.broadcast_id);
      if (!afterFailure || String(afterFailure.status || "draft") === "draft") {
        throw new Error(`broadcast_approval_failed:${response.status}`);
      }
    }
    const nextStatus = scheduleInFuture ? "scheduled" : "send_requested";
    const approved: DigestState = {
      ...state,
      status: nextStatus,
      approved_at: new Date().toISOString(),
      scheduled_for: scheduleInFuture ? scheduledFor : undefined,
      approved_content_sha256: await approvedContentHash(current),
      updated_at: new Date().toISOString(),
    };
    await saveDigestState(env, approved);
    return reviewShell("Edition approved", `
      <div style="max-width:680px;padding:30px;border:1px solid #b9dcca;border-radius:12px;background:#e4f3ec">
        <p style="margin:0 0 8px;color:#0f5f40;font-size:12px;font-weight:700;letter-spacing:.1em;text-transform:uppercase">Approved</p>
        <h1 style="margin:0 0 12px;font-family:Georgia,serif;font-size:40px;line-height:1.12;font-weight:500">${scheduleInFuture ? "Scheduled for Tuesday." : "Delivery requested."}</h1>
        <p style="margin:0;color:#315b49;line-height:1.6">${scheduleInFuture ? `Resend will deliver this edition at ${escapeHtml(scheduledFor)}.` : "The Tuesday delivery time had passed, so Resend was asked to send it now."}</p>
      </div>`);
  } catch (error) {
    console.error("broadcast_approval_failed", error instanceof Error ? error.message : String(error));
    return reviewShell("Approval failed", `<p style="color:#9a6b10;font-weight:700">Nothing was newly sent.</p><h1 style="font-family:Georgia,serif;font-size:38px;font-weight:500">Approval could not be completed.</h1><p style="color:#57564e">Please retry from the Monday preview. If this persists, inspect the Resend draft before taking any manual action.</p>`, 502);
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
    if (request.method === "POST" && url.pathname === "/broadcast/approve") return approve(request, env);
    if (request.method === "GET" && url.pathname === "/confirm") return confirm(request, env);
    if (request.method === "GET" && url.pathname === "/review") return review(request, env);
    if (request.method === "GET" && url.pathname === "/health") return Response.json({ ok: true, service: "behind-the-watt-newsletter" });
    if (url.pathname === "/") return Response.redirect(`${env.SITE_ORIGIN}/#subscribe`, 302);
    return new Response("Not found", { status: 404, headers: responseHeaders(env.SITE_ORIGIN) });
  },
} satisfies ExportedHandler<Env>;
