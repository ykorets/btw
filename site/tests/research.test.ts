import test from "node:test";
import assert from "node:assert/strict";
import { sourceLinkedEvents } from "../src/lib/research-events.ts";
test('the change feed never creates facility/null or source-less event links',()=>{
 const good={facility:'abilene',source_url:'https://example.org/permit'};
 const mirror={facilities:{facilities:[{slug:'abilene'}]},events:{events:[good,{facility:null,source_url:good.source_url},{facility:'missing',source_url:good.source_url},{facility:'abilene',source_url:null}]}};
 assert.deepEqual(sourceLinkedEvents(mirror.facilities.facilities, mirror.events.events),[good]);
});
