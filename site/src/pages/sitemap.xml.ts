import type { APIRoute } from "astro";
import { loadMirror } from "../lib/mirror";

export const prerender = true;

export const GET: APIRoute = async () => {
  const mirror = await loadMirror();
  const urls = [
    "https://behindthewatt.com/",
    "https://behindthewatt.com/data/",
    ...mirror.facilities.facilities.map(
      (facility: Record<string, any>) =>
        `https://behindthewatt.com/facility/${facility.slug}/`,
    ),
  ];
  const body = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ...urls.map((url) => `  <url><loc>${url}</loc></url>`),
    "</urlset>",
    "",
  ].join("\n");
  return new Response(body, {
    headers: { "Content-Type": "application/xml; charset=utf-8" },
  });
};
