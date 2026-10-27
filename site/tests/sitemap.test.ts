import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { checkSitemap } from "../scripts/check-sitemap.ts";
const origin="https://behindthewatt.com";
async function fixture(urls: string[], noindex=false) {
 const dir=await mkdtemp(join(tmpdir(),"btw-sitemap-"));
 await mkdir(join(dir,"newsletter"),{recursive:true});
 await writeFile(join(dir,"index.html"),`<html><head><link rel="canonical" href="${origin}/"></head></html>`);
 await writeFile(join(dir,"newsletter/index.html"),`<html><head><link rel="canonical" href="${origin}/newsletter/">${noindex?'<meta name="robots" content="noindex, follow">':''}</head></html>`);
 await writeFile(join(dir,"sitemap.xml"),`<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${urls.map(u=>`<url><loc>${u}</loc></url>`).join("")}</urlset>`);
 return dir;
}
test("a new indexable route cannot silently disappear from the sitemap",async()=>{
 const dir=await fixture([origin+"/"]);try{await assert.rejects(checkSitemap(dir),/Missing:.*newsletter/);}finally{await rm(dir,{recursive:true});}
});
test("utility noindex pages must be excluded, including when their canonical exists",async()=>{
 const dir=await fixture([origin+"/"],true);try{assert.deepEqual(await checkSitemap(dir),{indexable:1,noindex:1,redirects:0});
 await writeFile(join(dir,"sitemap.xml"),`<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>${origin}/</loc></url><url><loc>${origin}/newsletter/</loc></url></urlset>`);
 await assert.rejects(checkSitemap(dir),/Extra\/nonindexable/);}finally{await rm(dir,{recursive:true});}
});
test("duplicate and query-parameter URLs fail before publication",async()=>{
 for(const urls of [[origin+"/",origin+"/"],[origin+"/?test=1"]]){const dir=await fixture(urls);try{await assert.rejects(checkSitemap(dir),/duplicate or noncanonical/);}finally{await rm(dir,{recursive:true});}}
});
