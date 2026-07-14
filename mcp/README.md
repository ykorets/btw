# Behind the Watt MCP

Public, read-only remote MCP server for the published Behind the Watt mirror.
It never queries staging facts or the production database directly.

Endpoint: `https://mcp.behindthewatt.com/mcp`

Registry name: `io.github.ykorets/behind-the-watt`

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

Production deployment is handled by GitHub Actions whenever files under
`mcp/` reach `main`. The official MCP Registry entry is published manually or
by pushing a matching release tag such as `mcp-v1.0.0`; the tag must match the
version in `server.json`.
