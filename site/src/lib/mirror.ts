import { readFile } from "node:fs/promises";
import path from "node:path";

type Json = Record<string, any>;

function dataRoot() {
  return path.resolve(
    process.cwd(),
    process.env.BTW_DATA_DIR || "../_mirror/data",
  );
}

async function load(name: string): Promise<Json> {
  return JSON.parse(await readFile(path.join(dataRoot(), name), "utf8"));
}

async function loadOptional(name: string, fallback: Json): Promise<Json> {
  try { return await load(name); }
  catch (error: any) {
    if (error?.code === "ENOENT") return fallback;
    throw error;
  }
}

export async function loadMirror() {
  const [facilities, events, summary, coverage, announcements] = await Promise.all([
    load("facilities.json"),
    load("events.json"),
    load("summary.json"),
    load("coverage.json"),
    loadOptional("announcements.json", { summary: { projects: 0, reported_gw: 0 }, announcements: [] }),
  ]);
  return { facilities, events, summary, coverage, announcements };
}

function human(value: string): string {
  return value.replaceAll("_", " ").trim().replace(/\b\w/g, (c) => c.toUpperCase());
}

function displayDate(value?: string | null): string {
  if (!value) return "Date under verification";
  const date = new Date(value.includes("T") ? value : `${value}T00:00:00Z`);
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}

function facilityMw(facility: Json): number {
  return (facility.unit || []).reduce(
    (sum: number, unit: Json) =>
      sum + (unit.total_mw != null
        ? Number(unit.total_mw)
        : (unit.unit_count || 0) * Number(unit.mw_each || 0)),
    0,
  );
}

function monthsToPower(facility: Json): string {
  if (!facility.first_permit_filed || !facility.first_power) {
    return "Dates under verification";
  }
  const days =
    (Date.parse(facility.first_power) - Date.parse(facility.first_permit_filed)) /
    86_400_000;
  const months = Math.round(days / 30.4375);
  return `${months < 0 ? "−" : ""}${Math.abs(months)} months`;
}

const genreLabels: Record<string, string> = {
  satellite_scene: "Satellite scene",
  permit: "Permit",
  tceq_standard_permit_review: "Technical review",
  appeal_filing: "Appeal filing",
  court_filing: "Court filing",
  third_party_inventory: "Field inventory",
};

function coordinates(value?: string | null): [string, string] {
  const match = /^\((-?[0-9.]+),(-?[0-9.]+)\)$/.exec(value || "");
  return match ? [match[2], match[1]] : ["—", "—"];
}

function permitEnvelope(facility: Json): string {
  for (const permit of facility.permit || []) {
    const match = /(\d+)\s+turbines/i.exec(permit.permit_type || "");
    if (match) return `${match[1]} turbines`;
  }
  return "Not published as a structured field";
}

export function buildFacilityView(
  facility: Json,
  events: Json[],
  summary: Json,
  coverage: Json,
): Json {
  const capacity = facilityMw(facility);
  const [latitude, longitude] = coordinates(facility.geo);
  const satelliteSource = (facility.sources || []).find(
    (source: Json) => source.doc_genre === "satellite_scene",
  );
  const satelliteEvent = events.find(
    (event) => event.facility === facility.slug && event.type === "satellite_observation",
  );
  const gasSource = (coverage.sources || []).find(
    (source: Json) => source.adapter === "gas_ebb",
  );
  const supportedFacts = new Set(
    (facility.sources || []).flatMap((source: Json) => source.facts || []),
  );
  const timeline: Array<Json> = events
    .filter((event) => event.facility === facility.slug)
    .map((event) => ({ date: event.date, displayDate: displayDate(event.date), label: human(event.type || "Event"), headline: event.headline, url: event.source_url }));
  if (facility.first_power) timeline.push({ date: facility.first_power, displayDate: displayDate(facility.first_power), label: "Operation", headline: "First verified power date", url: null });
  for (const permit of facility.permit || []) {
    if (permit.filed_at) timeline.push({ date: permit.filed_at, displayDate: displayDate(permit.filed_at), label: "Permit", headline: `${permit.authority} ${permit.permit_no} filed`, url: null });
    if (permit.issued_at) timeline.push({ date: permit.issued_at, displayDate: displayDate(permit.issued_at), label: "Permit", headline: `${permit.authority} ${permit.permit_no} issued`, url: null });
  }
  timeline.sort((a, b) => b.date.localeCompare(a.date));
  const description = `Evidence dossier for ${facility.name}: ${capacity.toFixed(1)} MW published operating capacity, permits, events and source documents.`;
  return {
    ...facility,
    statusLabel: human(facility.status || "Under review"),
    location: [facility.county, facility.state].filter(Boolean).join(", "),
    capacityMw: capacity.toFixed(1),
    capacityNote: supportedFacts.has("unit.total_mw")
      ? "Uses a directly sourced cohort total where the source does not establish an individual unit rating."
      : supportedFacts.has("unit.unit_count") && supportedFacts.has("unit.mw_each")
        ? "Calculated from published unit count × unit rating. The export contains receipts for both field types, but row-level coverage can still vary."
        : "Calculated from published unit count × unit rating. The configuration is public, but receipts for both unit-level inputs are not yet present in the export.",
    asOf: summary.as_of || "under review",
    description,
    ttp: monthsToPower(facility),
    publishedUnits: (facility.unit || []).reduce((sum: number, unit: Json) => sum + (unit.unit_count || 0), 0) || "Under verification",
    permitEnvelope: permitEnvelope(facility),
    equipment: (facility.unit || []).map((unit: Json) => ({
      name: [unit.oem, unit.model].filter(Boolean).join(" ") || "Configuration under review",
      details: [
        `${unit.unit_count || "—"} units`,
        unit.mw_each != null ? `${unit.mw_each} MW each` : null,
        unit.total_mw != null ? `${unit.total_mw} MW cohort total` : null,
        unit.hours_permitted != null ? `${Number(unit.hours_permitted).toLocaleString("en-US")} permitted hr/yr` : null,
      ].filter(Boolean).join(" · "),
    })),
    timeline,
    permits: (facility.permit || []).map((permit: Json) => ({
      ...permit,
      title: `${permit.authority || ""} ${permit.permit_no || ""}`.trim(),
      meta: [
        permit.status || "status under review",
        permit.filed_at ? `Filed ${displayDate(permit.filed_at)}` : null,
        permit.issued_at ? `Issued ${displayDate(permit.issued_at)}` : null,
      ].filter(Boolean).join(" · "),
      provenanceLabel: [...supportedFacts].some((fact) => String(fact).startsWith("permit."))
        ? "Field-level receipt exported"
        : "Field-level permit receipt not yet exported",
    })),
    sources: (facility.sources || []).map((source: Json) => ({
      ...source,
      label: genreLabels[source.doc_genre] || human(source.doc_genre || "Document"),
      factsLabel: (source.facts || []).map((fact: string) => human(fact.replaceAll(".", " "))).join(", "),
    })),
    satellite: {
      date: displayDate(satelliteEvent?.date),
      text: satelliteEvent?.headline || "A satellite source supports operating status; no dated observation is exported yet.",
      url: satelliteSource?.url || "#receipts",
      label: satelliteSource ? "Open public scene ↗" : "See receipts",
      coords: `${latitude}, ${longitude}`,
    },
    gasStatus: gasSource?.last_hit_at
      ? `Gas nomination monitor last completed a successful sweep on ${displayDate(gasSource.last_hit_at)}; facility-level series is not yet public.`
      : "Facility-level gas series is not yet part of the public export.",
  };
}
