"""btw_engine.tceq_records — TCEQ Records Online document lookup by permit.

TCEQ files every New Source Review document (application, technical review,
preliminary determination summary, draft conditions, MAERT, final action)
in Records Online under the permit number as "Primary ID". The public search
UI asks for an anonymous access id first, then runs the search; the file
link (``TCEQ_APD_SEARCH_GET_FILE``) is a plain public GET and needs no
session, so the archive fetcher can download it like any other URL.

Only document types that can carry equipment or permit facts are returned;
modeling runs and public-notice packets are skipped on purpose.
"""

from __future__ import annotations

import html
import re

import httpx

BASE = "https://records.tceq.texas.gov/cs/idcplg"
NSR_PERMIT_SERIES = "1081"
UA = {"User-Agent": "btw-engine/0.1 (+https://behindthewatt.com; public-records archive)"}

# Records Online document title -> archive genre.
DOC_GENRES = {
    "Preliminary Determination Summary": "tceq_preliminary_determination",
    "Technical Review": "tceq_standard_permit_review",
    "Final Action": "tceq_standard_permit_final_action",
    "Initial Application": "tceq_nsr_application",
    "Conditions": "tceq_special_conditions",
    "MAERT": "tceq_maert",
}


def file_url(parent: str | None, did: str) -> str:
    """Public file link: newer items via their APD parent, older ones by dID."""
    if parent:
        return (f"{BASE}?IdcService=TCEQ_APD_SEARCH_GET_FILE"
                f"&xAPDParent={parent}&searchType=External")
    return f"{BASE}?IdcService=TCEQ_EXTERNAL_SEARCH_GET_FILE&dID={did}&Rendition=Web"


def _cell_text(cell: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", cell)).split())


def parse_results(page: str) -> list[dict]:
    """Search result HTML -> [{did, parent, title, genre, primary_id, ...}]."""
    found = re.search(r"Found\s+(\d+)", page)
    if found is None:
        raise ValueError("TCEQ Records Online: unrecognized search response")
    docs: dict[str, dict] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.S):
        did = re.search(r'name="dID"\s+value="(\d+)"', row)
        title = re.search(r'name="dDocTitle"\s+value="([^"]*)"', row)
        parent = re.search(r"xAPDParent=(\d+)", row)
        if not (did and title):
            continue
        cells = [_cell_text(c) for c in
                 re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)]
        cells = [c for c in cells if c]
        date = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", " ".join(cells))
        try:
            series_at = cells.index("AIR / New Source Review Permit")
            primary_id = cells[series_at + 1]
        except (ValueError, IndexError):
            primary_id = None
        doc_title = html.unescape(title.group(1)).strip()
        docs[did.group(1)] = {
            "did": did.group(1),
            "parent": parent.group(1) if parent else None,
            "title": doc_title,
            "genre": DOC_GENRES.get(doc_title),
            "primary_id": primary_id,
            "date": (f"{date.group(3)}-{int(date.group(1)):02d}-"
                     f"{int(date.group(2)):02d}") if date else None,
            "url": file_url(parent.group(1) if parent else None, did.group(1)),
        }
    if int(found.group(1)) and not docs:
        raise ValueError("TCEQ Records Online: results reported but no rows parsed")
    return list(docs.values())


class RecordsClient:
    """One anonymous Records Online session (access id + cookies)."""

    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(
            timeout=90, headers=UA, follow_redirects=True)
        self.ip = ""
        try:
            self.ip = self.client.get(
                "https://api.ipify.org", params={"format": "json"},
                timeout=15).json().get("ip", "")
        except Exception:  # noqa: BLE001 — the agency also accepts blank IP lookups
            self.ip = ""

    def _access_id(self) -> str:
        response = self.client.get(BASE, params={
            "IdcService": "TCEQ_SEARCH_ADD_ACCESS", "clientIP": self.ip,
            "searchType": "External", "IsJson": 1})
        response.raise_for_status()
        return str(response.json()["LocalData"]["accessID"])

    def search(self, primary_id: str, attempts: int = 3) -> list[dict]:
        """The agency search intermittently answers with a generic server
        error page; a fresh access id usually succeeds."""
        last: Exception | None = None
        for _attempt in range(attempts):
            try:
                return self._search_once(primary_id)
            except (ValueError, httpx.HTTPError) as exc:
                last = exc
        raise RuntimeError(f"TCEQ Records Online lookup failed for {primary_id}: {last}")

    def _search_once(self, primary_id: str) -> list[dict]:
        params = {
            "IdcService": "TCEQ_PERFORM_SEARCH", "xIdcProfile": "Record",
            "IsExternalSearch": 1, "sortSearch": "false", "newSearch": "true",
            "clientIP": self.ip, "accessID": self._access_id(),
            "xRecordSeries": NSR_PERMIT_SERIES, "xInsightDocumentType": 0,
            "xMedia": 0, "select0": "xPrimaryID", "input0": primary_id,
            "operator": "AND", "ftx": "",
        }
        for i in (1, 2, 3):
            params[f"select{i}"] = ""
            params[f"input{i}"] = ""
        response = self.client.get(BASE, params=params)
        response.raise_for_status()
        return [doc for doc in parse_results(response.text)
                if doc["primary_id"] in (None, primary_id)]
