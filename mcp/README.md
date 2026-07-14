# Behind the Watt MCP

Public, read-only remote MCP server for the published Behind the Watt mirror.
It never queries staging facts or the production database directly.

Endpoint: `https://mcp.behindthewatt.com/mcp`

Tools:

- `search_facilities` — filter BTW-verified facilities.
- `get_facility` — retrieve a full dossier and its provenance receipts.
- `get_announcements` — retrieve the separately classified third-party pipeline.
- `get_dataset_summary` — retrieve the canonical dataset date and totals.

The server uses stateless Streamable HTTP through Cloudflare Workers. Because
every tool is read-only and the underlying dataset is CC BY 4.0 public data, it
does not require authentication. All responses come from the same `/api/v1/`
snapshot used by the website.

Development:

```sh
npm install
npm run type-check
npm run dev
```
