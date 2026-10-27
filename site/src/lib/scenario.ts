/** Illustrative model assumptions; bounds match the visible controls. */
export const controls = [
  { id: "capex", value: 1600, min: 800, max: 2500, step: 50 },
  { id: "hr", value: 9500, min: 7500, max: 11500, step: 100 },
  { id: "gas", value: 4, min: 2, max: 8, step: .25 },
  { id: "om", value: 10, min: 5, max: 20, step: 1 },
  { id: "cf", value: 90, min: 50, max: 95, step: 1 },
  { id: "grid", value: 65, min: 40, max: 120, step: 1 },
  { id: "mo", value: 20, min: 0, max: 36, step: 1 },
  { id: "rev", value: 20, min: 2, max: 30, step: 1 },
  { id: "margin", value: 20, min: 0, max: 100, step: 1 },
] as const;

export function readScenario(params = new URLSearchParams()): Record<string, number> {
  return Object.fromEntries(controls.map(c => {
    const raw = params.get(c.id);
    const number = raw == null || raw.trim() === "" ? NaN : Number(raw);
    const value = Number.isFinite(number) && number >= c.min && number <= c.max
      ? c.min + Math.round((number - c.min) / c.step) * c.step : c.value;
    return [c.id, value];
  }));
}

export function scenarioParams(values: Record<string, number>): string {
  const safe = readScenario(new URLSearchParams(Object.entries(values).map(([key, value]) => [key, String(value)])));
  return new URLSearchParams(controls.map(c => [c.id, String(safe[c.id])])).toString();
}
