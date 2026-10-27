export type RecordData = Record<string, any>;

export function unitMw(unit: RecordData): number | null {
  if (unit.total_mw != null) return Number(unit.total_mw);
  if (unit.unit_count == null || unit.mw_each == null) return null;
  return Number(unit.unit_count) * Number(unit.mw_each);
}

export function capacity(facility: RecordData) {
  const values = (facility.unit || []).map(unitMw) as Array<number | null>;
  const known = values.filter((value): value is number => value != null && Number.isFinite(value));
  return { mw: known.length ? known.reduce((sum, value) => sum + value, 0) : null,
    partial: known.length < values.length };
}

export function capacityLabel(facility: RecordData): string {
  const { mw, partial } = capacity(facility);
  return mw == null ? "Unknown" : `${partial ? "≥" : ""}${mw.toLocaleString("en-US", { maximumFractionDigits: 1 })}`;
}

export function equipmentLabel(unit: RecordData): string {
  const name = [unit.oem, unit.model].filter(Boolean).join(" ") || "model not published";
  return `${unit.unit_count ?? "Unknown count"}× ${name}`;
}

export function hasReceipt(facility: RecordData, field: string): boolean {
  return (facility.sources || []).some((source: RecordData) => (source.facts || []).includes(field));
}

export function monthsToPower(facility: RecordData): number | null {
  if (!facility.first_permit_filed || !facility.first_power ||
      !hasReceipt(facility, "facility.first_permit_filed") || !hasReceipt(facility, "facility.first_power")) return null;
  const span = Date.parse(facility.first_power) - Date.parse(facility.first_permit_filed);
  return Number.isFinite(span) ? Math.round(span / 86_400_000 / 30.4375) : null;
}

export function dateLabel(value?: string | null): string {
  if (!value) return "Date under verification";
  const time = Date.parse(value.includes("T") ? value : `${value}T00:00:00Z`);
  if (!Number.isFinite(time)) return "Date under verification";
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }).format(time);
}

export function costModel(input: RecordData) {
  const { capex, heatRate, gas, om, capacityFactor, grid, months, revenue, margin } = input;
  const rate = .10, years = 15;
  const crf = rate * (1 + rate) ** years / ((1 + rate) ** years - 1);
  const fuel = heatRate / 1000 * gas;
  const capital = capex * 1000 * crf / (8760 * capacityFactor);
  const btm = fuel + capital + om;
  const premium = btm - grid;
  return { fuel, capital, btm, premium,
    premium10: Math.max(0, premium) * 8760 * capacityFactor * 10 / 1e6,
    contribution: months / 12 * revenue * margin };
}
