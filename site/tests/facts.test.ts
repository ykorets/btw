import test from "node:test";
import assert from "node:assert/strict";
import { capacity, capacityLabel, unitMw, equipmentLabel, monthsToPower, costModel } from "../src/lib/facts.ts";

test("direct cohort totals survive absent per-unit ratings", () => {
  const facility = {unit: [{unit_count: 27, total_mw: 495, mw_each: null}]};
  assert.equal(capacity(facility).mw, 495);
  assert.equal(capacityLabel(facility), "495");
  assert.equal(capacity({unit: [{unit_count: 5, mw_each: 38}, {unit_count: 5, mw_each: 34.1}]}).mw, 360.5);
});

test("unknown capacity stays unknown; partial coverage is visibly bounded", () => {
  assert.equal(unitMw({unit_count: 27, mw_each: null}), null);
  assert.equal(capacityLabel({unit: []}), "Unknown");
  assert.equal(capacityLabel({unit: [{unit_count: 27}]}), "Unknown");
  assert.equal(capacityLabel({unit: [{total_mw: 495}, {unit_count: 2}]}), "≥495");
  assert.equal(capacityLabel({unit: [{total_mw: 0}]}), "0");
  assert.equal(equipmentLabel({unit_count: 27, model: null, oem: null}), "27× model not published");
});

test("legacy dates cannot become an unsupported time-to-power benchmark", () => {
  const facility = {first_permit_filed: "2024-08-01", first_power: "2025-09-01", sources: [] as any[]};
  assert.equal(monthsToPower(facility), null);
  facility.sources.push({facts: ["facility.first_permit_filed", "facility.first_power"]});
  assert.equal(monthsToPower(facility), 13);
});

test("earlier operation uses contribution margin, including zero margin and zero saved time", () => {
  const input = {capex: 1600, heatRate: 9500, gas: 4, om: 10, capacityFactor: .9, grid: 65, months: 20, revenue: 20, margin: .2};
  const result = costModel(input);
  assert.ok(Math.abs(result.fuel - 38) < 1e-10);
  assert.ok(Math.abs(result.contribution - 20 / 12 * 20 * .2) < 1e-10);
  assert.equal(costModel({...input, margin: 0}).contribution, 0);
  assert.equal(costModel({...input, months: 0}).contribution, 0);
  assert.equal(costModel({...input, grid: 1000}).premium10, 0);
});
