import test from "node:test";
import assert from "node:assert/strict";
import { readScenario, scenarioParams } from "../src/lib/scenario.ts";
import { costModel } from "../src/lib/facts.ts";

test("shared scenarios retain zero values and round-trip every visible assumption", () => {
  const values = {capex: 1800, hr: 8500, gas: 3.5, om: 9, cf: 92, grid: 68, mo: 0, rev: 18, margin: 0};
  assert.deepEqual(readScenario(new URLSearchParams(scenarioParams(values))), values);
});

test("public URL values cannot produce nonfinite, out-of-bounds or empty assumptions", () => {
  const values = readScenario(new URLSearchParams("gas=Infinity&cf=0&margin=&hr=<script>&capex=1000000&mo=-1&rev=NaN&om=9.3&grid=66&other=abc"));
  assert.deepEqual(values, {capex: 1600, hr: 9500, gas: 4, om: 9, cf: 90, grid: 66, mo: 20, rev: 20, margin: 20});
});

test("gas sensitivity changes fuel cost linearly; lower utilization raises capital per MWh", () => {
  const assumptions = {capex: 1600, heatRate: 9500, gas: 4, om: 10, capacityFactor: .9, grid: 65, months: 20, revenue: 20, margin: .2};
  const base = costModel(assumptions);
  assert.ok(Math.abs(costModel({...assumptions, gas: 5}).btm - base.btm - 9.5) < 1e-10);
  assert.ok(costModel({...assumptions, capacityFactor: .6}).capital > base.capital);
});
