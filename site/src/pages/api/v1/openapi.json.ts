import { json } from "../../../lib/api";

export const prerender = true;

const dataset = (description: string, contentType = "application/json") => ({
  get: {
    operationId: `get${description.replace(/[^a-z0-9]/gi, "")}`,
    summary: description,
    responses: {
      "200": {
        description: "Current public mirror snapshot",
        content: { [contentType]: { schema: { type: contentType === "text/csv" ? "string" : "object" } } },
      },
    },
  },
});

export const GET = () => json({
  openapi: "3.1.0",
  info: {
    title: "Behind the Watt Public Data API",
    version: "1.0.0",
    description: "Read-only, no-auth access to verified behind-the-meter power data, announcements, events and provenance.",
    license: { name: "CC BY 4.0", identifier: "CC-BY-4.0" },
  },
  servers: [{ url: "https://behindthewatt.com" }],
  paths: {
    "/api/v1/index.json": dataset("API manifest"),
    "/api/v1/facilities.json": dataset("Verified facilities with units permits and source receipts"),
    "/api/v1/announcements.json": dataset("Third party reported project pipeline"),
    "/api/v1/events.json": dataset("Chronological public events"),
    "/api/v1/summary.json": dataset("Verified fleet summary and dataset date"),
    "/api/v1/coverage.json": dataset("Source monitoring coverage"),
    "/api/v1/fleet.csv": dataset("Flat verified fleet CSV", "text/csv"),
  },
});
