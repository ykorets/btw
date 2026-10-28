import { json } from "../../../lib/api";

export const prerender = true;
export const GET = () => json({
  name: "Behind the Watt public data API",
  version: "v1",
  license: "CC BY 4.0",
  attribution: "Behind the Watt, behindthewatt.com",
  documentation: "https://behindthewatt.com/data/",
  openapi: "https://behindthewatt.com/api/v1/openapi.json",
  updated_from: "summary.as_of is the export date, not a blanket reverification date. Dated record_reviewed events describe the scope checked.",
  datasets: {
    facilities: "/api/v1/facilities.json",
    announcements: "/api/v1/announcements.json",
    events: "/api/v1/events.json",
    changes: "/api/v1/changes.json",
    research_projects: "/api/v1/research-projects.json",
    opsb_review: "/api/v1/opsb-review.json",
    summary: "/api/v1/summary.json",
    coverage: "/api/v1/coverage.json",
    fleet_csv: "/api/v1/fleet.csv",
  },
  semantics: {
    facilities: "Published equipment cohorts and field-level receipts. Capacity may be permitted or historically observed; it is not live delivered power.",
    announcements: "Third-party reported pipeline; not equivalent to verified operating capacity.",
    research_projects: "Selected primary-source project dossiers with regulator and operator evidence distinguished. Not added to the reviewed operating fleet.",
    changes: "Manually reviewed retrospective interpretations plus published mirror events. Event date, review date and export date are distinct; unreviewed collector candidates excluded.",
    null_values: "Unknown or not yet verified. Never infer zero from null.",
  },
});
