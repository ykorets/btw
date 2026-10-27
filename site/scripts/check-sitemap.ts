import { readFile, readdir } from "node:fs/promises";
import { resolve, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const ORIGIN = "https://behindthewatt.com";
function decode(value: string): string {
  return value.replace(/&(?:amp|quot|apos|lt|gt);/g, entity => ({"&amp;":"&","&quot;":"\"","&apos;":"'","&lt;":"<","&gt;":">"}[entity]!));
}
function attrs(tag: string): Record<string, string> {
  return Object.fromEntries([...tag.matchAll(/([\w-]+)\s*=\s*(["'])(.*?)\2/gs)].map(m => [m[1].toLowerCase(),decode(m[3])]));
}
function validUrl(value: string): boolean {
  try { const u = new URL(value); return u.origin === ORIGIN && !u.search && !u.hash && !u.username && !u.password && u.pathname.endsWith("/") && u.href === value; }
  catch { return false; }
}
async function htmlFiles(directory: string): Promise<string[]> {
  const entries = await readdir(directory, {withFileTypes:true});
  const nested = await Promise.all(entries.map(e => e.isDirectory() ? htmlFiles(resolve(directory,e.name)) : Promise.resolve(e.name.endsWith(".html") ? [resolve(directory,e.name)] : [])));
  return nested.flat();
}

export async function checkSitemap(directory: string) {
  const xml = await readFile(resolve(directory,"sitemap.xml"),"utf8");
  if (!/<urlset\b[^>]*xmlns=["']http:\/\/www\.sitemaps\.org\/schemas\/sitemap\/0.9["']/.test(xml)) throw Error("Sitemap namespace missing");
  const urls = [...xml.matchAll(/<loc>([^<]+)<\/loc>/g)].map(m => decode(m[1]));
  if (!urls.length || new Set(urls).size !== urls.length || urls.some(u => !validUrl(u))) throw Error("Sitemap contains empty, duplicate or noncanonical URLs");
  const canonical = new Set<string>();
  let noindex = 0, redirects = 0;
  for (const file of await htmlFiles(directory)) {
    const html = await readFile(file,"utf8");
    const head = /<head\b[^>]*>([\s\S]*?)<\/head>/i.exec(html)?.[1] || "";
    const tags = [...head.matchAll(/<(?:meta|link)\b[^>]*>/gi)].map(m => attrs(m[0]));
    if (tags.some(t => t["http-equiv"]?.toLowerCase() === "refresh")) { redirects++; continue; }
    if (tags.some(t => ["robots","googlebot"].includes(t.name?.toLowerCase()) && /\b(noindex|none)\b/i.test(t.content || ""))) { noindex++; continue; }
    const links = tags.filter(t => t.rel?.toLowerCase().split(/\s+/).includes("canonical"));
    const route = "/" + relative(directory,file).split(sep).join("/").replace(/index\.html$/,"");
    if (links.length !== 1 || links[0].href !== ORIGIN+route || !validUrl(links[0].href)) throw Error(`Invalid self-canonical: ${route}`);
    canonical.add(links[0].href);
  }
  const missing = [...canonical].filter(u => !urls.includes(u));
  const extra = urls.filter(u => !canonical.has(u));
  if (missing.length || extra.length) throw Error(`Sitemap mismatch. Missing: ${missing.join(", ") || "none"}. Extra/nonindexable: ${extra.join(", ") || "none"}`);
  return {indexable:canonical.size,noindex,redirects};
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const result = await checkSitemap(resolve("dist"));
  console.log(`Sitemap verified: ${result.indexable} canonical pages; excluded ${result.noindex} noindex pages and ${result.redirects} redirects.`);
}
