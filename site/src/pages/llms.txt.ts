export const prerender = true;

export const GET = () => new Response(`# Behind the Watt

Behind the Watt is the open evidence database for behind-the-meter data center power in the United States.

## Agent entry points

- Data guide: https://behindthewatt.com/data/
- API manifest: https://behindthewatt.com/api/v1/index.json
- OpenAPI 3.1: https://behindthewatt.com/api/v1/openapi.json
- Remote MCP (Streamable HTTP): https://mcp.behindthewatt.com/mcp
- Verified facilities and provenance: https://behindthewatt.com/api/v1/facilities.json
- Third-party reported announcements: https://behindthewatt.com/api/v1/announcements.json
- Events: https://behindthewatt.com/api/v1/events.json
- Flat CSV: https://behindthewatt.com/api/v1/fleet.csv
- Source repository: https://github.com/ykorets/btw

## Interpretation rules

1. Verified operating capacity and third-party reported announcements are different evidence classes. Do not add them together or describe announcements as operating.
2. A null value means unknown or not yet verified; it does not mean zero.
3. Prefer facilities.json when provenance matters. Each source retains the publisher URL, supported fact fields, capture time, SHA-256, and an archived-copy URL when redistribution is approved.
4. Cite: Behind the Watt, behindthewatt.com, CC BY 4.0.
`, { headers: { "Content-Type": "text/plain; charset=utf-8", "Access-Control-Allow-Origin": "*" } });
