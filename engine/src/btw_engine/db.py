"""Connection settings for the BTW data API (owned PostgreSQL + PostgREST).

The API sits behind Cloudflare Access (service token) and PostgREST checks
its own JWT, so every request carries both credentials.

Env:
  BTW_API_URL               base URL of the PostgREST API (no trailing path)
  BTW_API_KEY               PostgREST JWT for the service_role
  BTW_ACCESS_CLIENT_ID      Cloudflare Access service token id (optional
  BTW_ACCESS_CLIENT_SECRET  locally, required by the hosted API)
"""

import os


def url(path: str = "") -> str:
    """Absolute URL for a table, view or rpc/<fn> path."""
    base = os.environ["BTW_API_URL"].rstrip("/")
    return f"{base}/{path.lstrip('/')}" if path else base


def headers(extra: dict | None = None) -> dict:
    """Auth headers for the data API, merged with any request-specific ones."""
    key = os.environ["BTW_API_KEY"]
    h = {"Authorization": f"Bearer {key}"}
    client_id = os.environ.get("BTW_ACCESS_CLIENT_ID")
    if client_id:
        h["CF-Access-Client-Id"] = client_id
        h["CF-Access-Client-Secret"] = os.environ["BTW_ACCESS_CLIENT_SECRET"]
    if extra:
        h.update(extra)
    return h
