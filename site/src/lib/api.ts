import type { APIRoute } from "astro";
import { loadMirrorJson, loadMirrorText } from "./mirror";

const headers = {
  "Access-Control-Allow-Origin": "*",
  "Cache-Control": "public, max-age=300, s-maxage=3600",
  "X-Content-Type-Options": "nosniff",
};

export function mirrorJson(name: string): APIRoute {
  return async () => new Response(JSON.stringify(await loadMirrorJson(name), null, 2), {
    headers: { ...headers, "Content-Type": "application/json; charset=utf-8" },
  });
}

export function mirrorText(name: string, contentType: string): APIRoute {
  return async () => new Response(await loadMirrorText(name), {
    headers: { ...headers, "Content-Type": contentType },
  });
}

export function json(data: unknown): Response {
  return new Response(JSON.stringify(data, null, 2), {
    headers: { ...headers, "Content-Type": "application/json; charset=utf-8" },
  });
}
