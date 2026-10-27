import type { Env } from "./index.ts";

type PilotEnv = Pick<Env, "SITE_ORIGIN" | "RESEND_FROM" | "RESEND_API_KEY" | "EDITOR_EMAIL" | "SUBSCRIBE_RATE_LIMITER">;
export type PilotInquiry = { name: string; email: string; company: string; role: string; projects: string; question: string; source_path: string };
const encoder = new TextEncoder();
const MAX_BODY = 16_384;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function text(value: unknown, max: number, min = 0): string | null {
  if (typeof value !== "string") return min === 0 && value == null ? "" : null;
  const clean = value.trim();
  return clean.length >= min && clean.length <= max && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(clean) ? clean : null;
}

export function validatePilotInquiry(form: Record<string, unknown>): PilotInquiry | null {
  const name = text(form.name, 100, 2), company = text(form.company, 160, 2);
  const email = text(form.email, 254, 3)?.toLowerCase();
  const role = text(form.role, 120), projects = text(form.projects, 1500, 3), question = text(form.question, 2000, 10);
  const source_path = text(form.source_path, 160) || "/monitoring-pilot/";
  if (name == null || company == null || !email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)
    || role == null || projects == null || question == null || (form.consent !== true && form.consent !== "yes")
    || !/^\/[a-z0-9/-]*\/$/i.test(source_path)) return null;
  return {name, email, company, role, projects, question, source_path};
}

async function digest(value: string): Promise<string> {
  return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", encoder.encode(value))), b => b.toString(16).padStart(2, "0")).join("");
}

async function boundedText(body: ReadableStream<Uint8Array> | null, max = MAX_BODY): Promise<string> {
  if (!body) return "";
  const reader = body.getReader();
  const parts: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > max) { await reader.cancel(); throw new RangeError("body_too_large"); }
      parts.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const part of parts) { bytes.set(part, offset); offset += part.length; }
  return new TextDecoder("utf-8", {fatal: true}).decode(bytes);
}

export function pilotHeaders(origin: string): HeadersInit {
  return {"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Cache-Control": "no-store", "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'", "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff", "X-Robots-Tag": "noindex, nofollow"};
}

function reply(request: Request, env: PilotEnv, status: number, code: string, requestId?: string): Response {
  const headers = pilotHeaders(env.SITE_ORIGIN);
  if (request.headers.get("accept")?.includes("application/json")) {
    return Response.json({ok: status === 202, status: code, ...(requestId ? {request_id: requestId} : {})}, {status, headers});
  }
  const received = status === 202;
  const message = received ? "Your pilot inquiry has been received. We will review the requested scope before discussing a fee. No payment or newsletter subscription was created."
    : status === 429 ? "Too many requests. Please wait a minute and try again."
    : status < 500 ? "Your inquiry could not be accepted. Return to the form and check the required fields and privacy consent."
    : "Delivery could not be confirmed. Return to the form and retry the same request, or use the contact address on the page. Do not assume the inquiry was received.";
  return new Response(`<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>${received ? "Inquiry received" : "Inquiry not confirmed"} · Behind the Watt</title><body><main><h1>${received ? "Inquiry received" : "Inquiry not confirmed"}</h1><p>${message}</p><a href="${env.SITE_ORIGIN}/monitoring-pilot/#request">Return to Behind the Watt</a></main></body></html>`, {status, headers: {...headers, "Content-Type":"text/html; charset=utf-8"}});
}

export async function pilotInquiry(request: Request, env: PilotEnv): Promise<Response> {
  if (request.headers.get("origin") !== env.SITE_ORIGIN) return reply(request, env, 403, "forbidden");
  const kind = request.headers.get("content-type")?.split(";")[0].trim().toLowerCase();
  if (kind !== "application/json" && kind !== "application/x-www-form-urlencoded") return reply(request, env, 415, "unsupported_format");
  if (Number(request.headers.get("content-length")) > MAX_BODY) return reply(request, env, 413, "body_too_large");
  const ip = request.headers.get("cf-connecting-ip");
  if (ip && !(await env.SUBSCRIBE_RATE_LIMITER.limit({key: `pilot-ip:${await digest(ip)}`})).success) return reply(request, env, 429, "rate_limited");
  let form: Record<string, unknown>;
  try {
    const raw = await boundedText(request.body);
    const parsed = kind === "application/json" ? JSON.parse(raw) : Object.fromEntries(new URLSearchParams(raw));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return reply(request, env, 400, "invalid_request");
    form = parsed;
  } catch (error) { return reply(request, env, error instanceof RangeError ? 413 : 400, "invalid_request"); }
  if (form.website) return reply(request, env, 202, "filtered");
  const inquiry = validatePilotInquiry(form);
  if (!inquiry || (form.request_id && (typeof form.request_id !== "string" || !UUID.test(form.request_id)))) return reply(request, env, 400, "invalid_fields");
  if (!(await env.SUBSCRIBE_RATE_LIMITER.limit({key:`pilot-email:${await digest(inquiry.email)}`})).success) return reply(request, env, 429, "rate_limited");
  const reference = typeof form.request_id === "string" && form.request_id ? form.request_id.toLowerCase()
    : await digest(`pilot-v1:${new Date().toISOString().slice(0,10)}:${JSON.stringify(inquiry)}`);
  // Fixed recipient and subject; submitted values are plain text only. No contact creation or caller email.
  const message = {from: env.RESEND_FROM, to: [env.EDITOR_EMAIL], reply_to: inquiry.email,
    subject: `BTW pilot inquiry — ${reference}`,
    text: `Paid pilot scope request\nReference: ${reference}\n\nName: ${inquiry.name}\nCompany: ${inquiry.company}\nEmail: ${inquiry.email}\nRole: ${inquiry.role || "Not supplied"}\n\nProjects / states:\n${inquiry.projects}\n\nResearch question:\n${inquiry.question}\n\nEntry page: ${inquiry.source_path}\nConsent: inquiry handling and reply; no newsletter enrollment.\n\nThis is a nonbinding request. Confirm coverage, scope and fee before acceptance.`,
    tags: [{name:"category",value:"pilot_inquiry"}]};
  try {
    const sent = await fetch("https://api.resend.com/emails", {method:"POST", signal: AbortSignal.timeout(10_000),
      headers:{Authorization:`Bearer ${env.RESEND_API_KEY}`,"Content-Type":"application/json","Idempotency-Key":`btw-pilot-${reference}`}, body:JSON.stringify(message)});
    if (!sent.ok) { console.error(JSON.stringify({event:"pilot_delivery_unconfirmed",status:sent.status})); return reply(request,env,502,"delivery_unconfirmed"); }
    const receipt = JSON.parse(await boundedText(sent.body, 4096));
    if (typeof receipt.id !== "string" || !receipt.id) return reply(request,env,502,"delivery_unconfirmed");
    return reply(request,env,202,"received",reference);
  } catch { console.error(JSON.stringify({event:"pilot_delivery_unconfirmed"})); return reply(request,env,502,"delivery_unconfirmed"); }
}
