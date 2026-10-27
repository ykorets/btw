import assert from "node:assert/strict";
import test from "node:test";
import worker from "../src/index.ts";
import { validatePilotInquiry } from "../src/pilot.ts";

const inquiry = {name:"Synthetic QA",company:"BTW internal verification",email:"test@example.com",role:"Research",projects:"Abilene, Texas",question:"TEST ONLY: verify source-linked pilot intake. No sales follow-up.",consent:"yes",source_path:"/monitoring-pilot/",request_id:"7279a0ca-ad6a-4af1-b11e-5eeeb32246c5"};
const env = {SITE_ORIGIN:"https://behindthewatt.com",RESEND_FROM:"BTW <weekly@updates.behindthewatt.com>",EDITOR_EMAIL:"owner@example.com",RESEND_API_KEY:"test-key",SUBSCRIBE_RATE_LIMITER:{limit:async()=>({success:true})}};
function request(data: unknown=inquiry, headers: Record<string,string>={}) {
 return new Request("https://newsletter.behindthewatt.com/pilot-inquiry",{method:"POST",headers:{Origin:env.SITE_ORIGIN,"Content-Type":"application/json",Accept:"application/json",...headers},body:JSON.stringify(data)});
}
async function run(req: Request, overrides={}) { return worker.fetch(req,{...env,...overrides} as never); }
async function captured(fn:(calls:any[])=>Promise<void>, result:()=>Promise<Response>=async()=>Response.json({id:"email-test"})) {
 const original=globalThis.fetch;const calls:any[]=[];
 globalThis.fetch=async(url,init)=>{calls.push({url,headers:new Headers(init?.headers),body:JSON.parse(String(init?.body))});return result();};
 try { await fn(calls); } finally { globalThis.fetch=original; }
}

test("pilot delivery uses fixed owner recipient, plain text and reply-to; never creates contacts",{concurrency:false},async()=>{
 await captured(async calls=>{
  const r=await run(request({...inquiry,to:"victim@example.com",company:"<script>test</script>"}));assert.equal(r.status,202);
  assert.deepEqual(await r.json(),{ok:true,status:"received",request_id:inquiry.request_id});
  assert.equal(calls.length,1);assert.equal(calls[0].url,"https://api.resend.com/emails");
  assert.deepEqual(calls[0].body.to,[env.EDITOR_EMAIL]);assert.equal(calls[0].body.reply_to,inquiry.email);
  assert.equal(calls[0].body.html,undefined);assert.match(calls[0].body.text,/<script>test<\/script>/);
  assert.equal(calls[0].body.subject,`BTW pilot inquiry — ${inquiry.request_id}`);
 });
});

test("retries preserve identical provider idempotency key and payload",{concurrency:false},async()=>{
 await captured(async calls=>{
  assert.equal((await run(request())).status,202);assert.equal((await run(request())).status,202);
  assert.equal(calls[0].headers.get("Idempotency-Key"),`btw-pilot-${inquiry.request_id}`);
  assert.equal(calls[1].headers.get("Idempotency-Key"),calls[0].headers.get("Idempotency-Key"));assert.deepEqual(calls[0].body,calls[1].body);
 });
});

test("validation rejects missing consent, short scope, header injection, oversized fields and external source path",()=>{
 for(const changes of [{consent:"no"},{email:"x@example.com\r\nBcc:y@example.com"},{projects:"x"},{question:"short"},{name:"a"},{company:"x"},{role:"x".repeat(121)},{source_path:"https://evil.example/"}]) assert.equal(validatePilotInquiry({...inquiry,...changes}),null);
 assert.ok(validatePilotInquiry({...inquiry,role:""}));
});

test("origin, format, shape, invalid UUID and oversized payload fail before delivery",{concurrency:false},async()=>{
 await captured(async calls=>{
  assert.equal((await run(request(inquiry,{Origin:"https://evil.example"}))).status,403);
  const noOrigin=request();noOrigin.headers.delete("Origin");assert.equal((await run(noOrigin)).status,403);
  assert.equal((await run(request(inquiry,{"Content-Type":"multipart/form-data"}))).status,415);
  assert.equal((await run(request([]))).status,400);
  assert.equal((await run(request({...inquiry,request_id:"bad"}))).status,400);
  assert.equal((await run(request({...inquiry,question:"x".repeat(17000)}))).status,413);
  assert.equal(calls.length,0);
 });
});

test("honeypot is filtered and never delivers or reports a received lead",{concurrency:false},async()=>{
 await captured(async calls=>{const r=await run(request({...inquiry,website:"https://spam.example"}));assert.equal(r.status,202);assert.equal((await r.json() as any).status,"filtered");assert.equal(calls.length,0);});
});

test("IP and email limits block before delivery without storing raw identifiers",{concurrency:false},async()=>{
 await captured(async calls=>{
  const keys:string[]=[];const limiter={limit:async({key}:{key:string})=>{keys.push(key);return {success:false};}};
  assert.equal((await run(request(inquiry,{"CF-Connecting-IP":"192.0.2.1"}),{SUBSCRIBE_RATE_LIMITER:limiter})).status,429);
  assert.equal((await run(request(),{SUBSCRIBE_RATE_LIMITER:limiter})).status,429);
  assert.match(keys[0],/^pilot-ip:[a-f0-9]{64}$/);assert.match(keys[1],/^pilot-email:[a-f0-9]{64}$/);assert.equal(calls.length,0);
 });
});

test("provider rejection, conflicting retry, timeout and malformed receipt never report success",{concurrency:false},async()=>{
 for(const result of [async()=>new Response("private provider error",{status:500}),async()=>new Response("conflict",{status:409}),async()=>{throw new Error("timeout");},async()=>Response.json({})]) {
  await captured(async()=>{const r=await run(request());assert.equal(r.status,502);assert.deepEqual(await r.json(),{ok:false,status:"delivery_unconfirmed"});},result);
 }
});

test("native HTML form receives a noindex confirmation with no echoed PII",{concurrency:false},async()=>{
 await captured(async()=>{
  const r=await run(new Request("https://newsletter.behindthewatt.com/pilot-inquiry",{method:"POST",headers:{Origin:env.SITE_ORIGIN,"Content-Type":"application/x-www-form-urlencoded",Accept:"text/html"},body:new URLSearchParams(inquiry)}));
  assert.equal(r.status,202);assert.equal(r.headers.get("X-Robots-Tag"),"noindex, nofollow");
  const html=await r.text();assert.match(html,/Inquiry received/);assert.equal(html.includes(inquiry.email),false);
 });
});

test("preflight and method boundary are confined to the public intake route",async()=>{
 assert.equal((await run(new Request("https://newsletter.behindthewatt.com/pilot-inquiry",{method:"OPTIONS",headers:{Origin:env.SITE_ORIGIN}}))).status,204);
 assert.equal((await run(new Request("https://newsletter.behindthewatt.com/pilot-inquiry",{method:"OPTIONS",headers:{Origin:"https://evil.example"}}))).status,403);
 assert.equal((await run(new Request("https://newsletter.behindthewatt.com/pilot-inquiry"))).status,405);
});
