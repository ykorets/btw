import { json } from "../../../lib/api";

export const prerender = true;
export const GET = () => json({
  name: "Behind the Watt public data API",
  version: "v1",
  license: "CC BY 4.0",
  attribution: "Behind the Watt, behindthewatt.com",
  documentation: "https://behindthewatt.com/data/",
  openapi: "https://behindthewatt.com/api/v1/openapi.json",
  updated_from: "The as_of field in summary.json is the canonical dataset date.",
  datasets: {
    facilities: "/api/v1/facilities.json",
    announcements: "/api/v1/announcements.json",
    events: "/api/v1/events.json",
    summary: "/api/v1/summary.json",
    coverage: "/api/v1/coverage.json",
    fleet_csv: "/api/v1/fleet.csv",
  },
  semantics: {
    facilities: "BTW-verified facilities and field-level source receipts.",
    announcements: "Third-party reported pipeline; not equivalent to verified operating capacity.",
    null_values: "Unknown or not yet verified. Never infer zero from null.",
  },
});
