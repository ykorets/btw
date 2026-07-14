import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { createMcpHandler } from "agents/mcp";
import { z } from "zod";

const API = "https://behindthewatt.com/api/v1";

type Item = Record<string, any>;

async function dataset(name: string): Promise<Item> {
  const response = await fetch(`${API}/${name}`, {
    headers: { Accept: "application/json", "User-Agent": "BehindTheWatt-MCP/1.0" },
  });
  if (!response.ok) throw new Error(`BTW API ${name} returned ${response.status}`);
  return response.json();
}

function capacityMw(facility: Item): number {
  return (facility.unit || []).reduce((sum: number, unit: Item) =>
    sum + Number(unit.total_mw ?? ((unit.unit_count || 0) * (unit.mw_each || 0))), 0);
}

function result(value: unknown) {
  return { content: [{ type: "text" as const, text: JSON.stringify(value, null, 2) }] };
}

function createServer() {
  const server = new McpServer({ name: "Behind the Watt", version: "1.0.0" });

  server.registerTool("search_facilities", {
    description: "Search BTW-verified behind-the-meter power facilities. Returns published facts and capacity; announcements are not included.",
    inputSchema: {
      query: z.string().optional().describe("Name, developer, offtaker, county or state text"),
      state: z.string().length(2).optional().describe("Two-letter US state code"),
      status: z.string().optional().describe("Published facility status, for example operating"),
      min_mw: z.number().nonnegative().optional(),
    },
  }, async ({ query, state, status, min_mw }) => {
    const data = await dataset("facilities.json");
    const needle = query?.toLowerCase();
    const facilities = (data.facilities || []).filter((facility: Item) => {
      const haystack = [facility.name, facility.developer, facility.offtaker, facility.county, facility.state, ...(facility.aliases || [])].join(" ").toLowerCase();
      return (!needle || haystack.includes(needle)) &&
        (!state || facility.state === state.toUpperCase()) &&
        (!status || facility.status === status) &&
        (min_mw == null || capacityMw(facility) >= min_mw);
    }).map((facility: Item) => ({
      slug: facility.slug, name: facility.name, state: facility.state,
      county: facility.county, status: facility.status,
      capacity_mw: capacityMw(facility), developer: facility.developer,
      dossier: `https://behindthewatt.com/facility/${facility.slug}/`,
    }));
    return result({ count: facilities.length, facilities });
  });

  server.registerTool("get_facility", {
    description: "Get one verified facility dossier including units, permits, quotes, source URLs, SHA-256 checksums and archived evidence links.",
    inputSchema: { slug: z.string().min(1) },
  }, async ({ slug }) => {
    const data = await dataset("facilities.json");
    const facility = (data.facilities || []).find((item: Item) => item.slug === slug);
    if (!facility) return result({ error: "facility_not_found", slug });
    return result({ ...facility, capacity_mw: capacityMw(facility) });
  });

  server.registerTool("get_announcements", {
    description: "Get the separately classified third-party reported project pipeline. These records are not BTW-verified operating capacity.",
    inputSchema: {
      state: z.string().length(2).optional(),
      limit: z.number().int().min(1).max(100).default(25),
    },
  }, async ({ state, limit }) => {
    const data = await dataset("announcements.json");
    const rows = (data.announcements || [])
      .filter((item: Item) => !state || item.state === state.toUpperCase())
      .slice(0, limit);
    return result({ classification: data.classification, summary: data.summary, count: rows.length, announcements: rows });
  });

  server.registerTool("get_dataset_summary", {
    description: "Get the canonical dataset date, verified fleet totals, methodology, license and citation.",
    inputSchema: {},
  }, async () => result(await dataset("summary.json")));

  return server;
}

export default {
  fetch(request: Request, env: unknown, ctx: ExecutionContext) {
    const url = new URL(request.url);
    if (url.pathname === "/") {
      return Response.redirect("https://behindthewatt.com/data/", 302);
    }
    if (url.pathname !== "/mcp") return new Response("Not found", { status: 404 });
    return createMcpHandler(createServer())(request, env, ctx);
  },
};
