"""btw_engine.watch — adapter runtime (M4, architecture §4.1).

A watcher turns a registry into `candidate` rows: (source_id, external_id)
unique, so daily reruns are idempotent. Parsers are pure functions over text —
CI replays them against fixtures in engine/tests/fixtures; a parser that stops
understanding its fixture fails CI before it fails in production.

Adapter configs live in engine/adapters/*.yaml (id, kind, adapter, url,
schedule, slo_interval_days, params, keywords). Entries with `status: stub`
are skipped.

Usage:
  python -m btw_engine.watch                 # run all live adapters
  python -m btw_engine.watch --source tceq-nsr-stdpmt
  python -m btw_engine.watch --dry-run       # print candidates, no DB writes
  python -m btw_engine.watch --slo           # staleness check; exit 1 if stale

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY; optional HEARTBEAT_URL,
COURTLISTENER_TOKEN.
"""

import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import random
import re
import sys
import time
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

import httpx
import yaml

ADAPTER_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "adapters")

# DIS (and some state WAFs) reject default client fingerprints.
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _rest(method: str, path: str, **kw) -> httpx.Response:
    base = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/" + path
    key = os.environ["SUPABASE_SERVICE_KEY"]
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    headers.update(kw.pop("headers", {}))
    r = httpx.request(method, base, headers=headers, timeout=60, **kw)
    r.raise_for_status()
    return r


def load_sources() -> list[dict]:
    out = []
    for path in sorted(glob.glob(os.path.join(ADAPTER_DIR, "*.yaml"))):
        cfg = yaml.safe_load(open(path))
        if cfg.get("status"):  # any status (stub/recon_done/...) = not live
            continue
        out.append(cfg)
    return out


# ---------------------------------------------------------------- TCEQ NSR --

TCEQ_COLUMNS = [
    "program", "permit_no", "permit_type", "permit_status", "project_number",
    "customer_name", "legal_name", "cn_number", "project_type", "rcv_date",
    "complete_date", "renewal_date", "project_status", "project_name",
    "regulated_entity", "physical_location", "region", "county",
    "near_city", "rules",
]


def parse_tceq_ascii(text: str) -> list[dict]:
    """Parse the NSR search's ASCII (pipe-delimited) output.

    In the raw HTTP response every field sits on its own line inside the row
    (separated by \\r\\n\\t), rows are delimited by <br />, and empty dates are
    rendered as chains of &nbsp;. So: normalize entities, drop tags, collapse
    ALL whitespace, then split the stream at each "NSR|" row start. This also
    parses the browser-rendered one-row-per-line form (fixtures).
    """
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    rows = []
    for chunk in re.findall(r"NSR\|(?:(?!NSR\|).)*", text):
        parts = [p.strip() for p in chunk.split("|")]
        if len(parts) < 10:  # stray "NSR|" mention, not a data row
            continue
        if len(parts) < len(TCEQ_COLUMNS):
            parts += [""] * (len(TCEQ_COLUMNS) - len(parts))
        rows.append(dict(zip(TCEQ_COLUMNS, parts[: len(TCEQ_COLUMNS)])))
    return rows


def run_tceq_nsr(cfg: dict) -> list[dict]:
    p = cfg.get("params", {})
    today = dt.date.today()
    frm = today - dt.timedelta(days=int(p.get("window_days", 30)))
    form = {
        "RequestTimeout": "3000",
        "loc_cnty_name": "0", "proj_id": "", "tnrcc_region_cd": "0",
        "addn_num_txt": "", "addn_id_typ_txt": p.get("permit_type", ""),
        "cn_issue_to_txt": "", "proj_typ_txt": "", "cn_ref_num_txt": "",
        "account": "", "unit_id": "", "rn_ref_num_txt": "",
        "proj_status_txt": "PENDING",
        "date_option": "rcv_dt",
        "date_range_from": frm.strftime("%m/%d/%Y"),
        "date_range_to": today.strftime("%m/%d/%Y"),
        "sort_dir": "desc", "program": "NSR", "order_by": "rcv_dt",
        "out_form": "text",
    }
    r = httpx.post(cfg["url"], data=form, headers=BROWSER_HEADERS,
                   timeout=90, follow_redirects=True)
    r.raise_for_status()
    parsed = parse_tceq_ascii(r.text)
    if not parsed:
        raise RuntimeError("TCEQ legacy NSR: no recognized rows; do not record a successful sweep")
    kws = [k.lower() for k in cfg.get("keywords", [])]
    out = []
    for row in parsed:
        blob = " ".join(row.values()).lower()
        row["kw_hit"] = any(k in blob for k in kws)
        out.append({
            "external_id": f"{row['project_number']}",
            "url": cfg["url"],
            "title": f"{row['legal_name']} — {row['project_name']} "
                     f"({row['county']}, rcv {row['rcv_date']})",
            "payload": row,
        })
    return out


# Public anonymous-session contract shipped by the agency's Permit Search UI.
# Registry snapshots are review candidates, never approved generation facts.
TCEQ_PORTAL_API = "https://permit-search.tceq.texas.gov/psp-webservices"
TCEQ_PORTAL_FIELDS = (
    "addnId", "aiPgmCd", "aiIdTypName", "aiIdNumber", "aiStatusCd",
    "pspStatusCd", "aiPermittedName", "aiIssueToName", "reRegNumber",
    "reName", "reCnty", "reCityName", "aiIssueToBeginDt",
)


def tceq_portal_candidates(rows: list[dict]) -> list[dict]:
    out = {}
    for row in rows:
        if row.get("aiPgmCd") != "AIRNSR":
            continue
        if not row.get("addnId") or not row.get("aiIdNumber"):
            raise ValueError("TCEQ portal: incomplete registry identity")
        snapshot = {k: row.get(k) for k in TCEQ_PORTAL_FIELDS}
        digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()[:16]
        key = f"portal:{row['addnId']}:{digest}"
        out[key] = {
            "external_id": key,
            "url": f"https://permit-search.tceq.texas.gov/?addnId={row['addnId']}",
            "title": f"{row['aiPermittedName']} — {row['aiIdNumber']} ({row.get('pspStatusCd', 'unknown')})",
            "payload": {"registry_snapshot": snapshot, "kw_hit": True,
                        "evidence_basis": "registry_status_not_operating_capacity"},
        }
    return list(out.values())


def run_tceq_portal(cfg: dict) -> list[dict]:
    params = cfg.get("params", {})
    rows = []
    with httpx.Client(timeout=60, headers={"AppName": "psp-frontend"}) as client:
        token = client.get(TCEQ_PORTAL_API + "/v1/token", params={"appName": "psp-frontend"})
        token.raise_for_status()
        client.headers["Authorization"] = "Bearer " + token.text.strip('"')
        for keyword in params["queries"]:
            expected_total, collected = None, 0
            for page in range(int(params.get("max_pages", 25))):
                response = client.post(TCEQ_PORTAL_API + "/v1/search/permitsWithPages", json={
                    "keyword": keyword, "normalizedAddress": "", "radius": None,
                    "latitude": None, "longitude": None,
                    "permitStatus": ["NEW APPLICATION", "ISSUED PERMIT", "RENEWAL/AMENDMENT"],
                    "mediaPrograms": [{"mediaCd": "AIR", "programDesc": ["Air New Source"]}],
                    "pageable": {"paged": True, "pageNumber": page, "pageSize": 100},
                })
                response.raise_for_status()
                data = response.json()
                if (not isinstance(data, dict) or not isinstance(data.get("content"), list)
                        or not isinstance(data.get("last"), bool)
                        or data.get("number") != page
                        or not isinstance(data.get("totalElements"), int)):
                    raise ValueError("TCEQ portal: unrecognized pagination response")
                if not data["content"] and data["totalElements"] != 0:
                    raise ValueError("TCEQ portal: unexpectedly empty page")
                if expected_total is None:
                    expected_total = data["totalElements"]
                elif expected_total != data["totalElements"]:
                    raise RuntimeError("TCEQ portal: result count changed during pagination; retry next sweep")
                collected += len(data["content"])
                rows.extend(data["content"])
                if data["last"]:
                    if collected != expected_total:
                        raise RuntimeError("TCEQ portal: incomplete result count")
                    break
            else:
                raise RuntimeError("TCEQ portal: page limit reached; incomplete sweep")
    return tceq_portal_candidates(rows)


# --------------------------------------------------------------- OPSB case --

class _DocketRows(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.cells, self.cell, self.href = [], [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.cells, self.href = [], None
        elif tag == "td":
            self.cell = []
        elif tag == "a" and self.cell is not None:
            href = dict(attrs).get("href", "")
            parsed = urlparse(href)
            if (parsed.path.lower().endswith("documentrecord.aspx")
                    and (not parsed.netloc or parsed.netloc == "dis.puc.state.oh.us")):
                self.href = href

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self.cell is not None:
            self.cells.append(re.sub(r"\s+", " ", "".join(self.cell)).strip())
            self.cell = None
        elif tag == "tr":
            if self.href and len(self.cells) >= 2:
                self.rows.append((self.href, self.cells[:]))
            self.cells, self.cell, self.href = [], None, None


def parse_opsb_case_html(html: str, case_no: str) -> list[dict]:
    """Read the agency's View All docket grid, independent of HTML spacing."""
    if re.search(r"Request Rejected|requested URL was rejected|captcha|access denied", html, re.I):
        raise RuntimeError(f"OPSB {case_no}: public docket access blocked; no successful sweep")
    parser = _DocketRows()
    parser.feed(html)
    out = []
    for rel_url, cells in parser.rows:
        qs = {k.lower(): v for k, v in parse_qs(urlparse(rel_url).query).items()}
        doc_id = qs.get("docid", [""])[0]
        filed, summary = cells[:2]
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", doc_id) or not re.fullmatch(r"\d{2}/\d{2}/\d{4}", filed):
            raise ValueError(f"OPSB {case_no}: unrecognized filing identity/date")
        out.append({
            "external_id": doc_id,
            "url": "https://dis.puc.state.oh.us/DocumentRecord.aspx?DocID=" + doc_id,
            "title": f"{case_no} {filed}: {summary[:160]}",
            "payload": {"case_no": case_no, "filed": filed,
                        "summary": summary},
        })
    return out


def validate_opsb_case(html: str, case_no: str, expected_count: int) -> list[dict]:
    """A rendered View All must match the requested case and initial count."""
    if not re.search(rf"\b{re.escape(case_no)}-EL-BLN\b", normalize_page_text(html)):
        raise RuntimeError(f"OPSB {case_no}: wrong or missing case identity")
    rows = parse_opsb_case_html(html, case_no)
    if (expected_count <= 0 or len(rows) != expected_count
            or len({r["external_id"] for r in rows}) != expected_count):
        raise RuntimeError(f"OPSB {case_no}: incomplete docket: expected {expected_count}, parsed {len(rows)}")
    return rows


def run_opsb_browser(cfg: dict) -> list[dict]:
    """Ordinary anonymous Chromium session through the agency's public UI.

    No saved cookies, credentials, proxy, custom fingerprint or challenge solver.
    DIS's normal JavaScript/session navigation runs in an ephemeral browser.
    Interactive challenges and partial/changed grids fail closed.
    """
    from playwright.sync_api import sync_playwright

    all_rows = {}
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=False)
        try:
            page = browser.new_page()
            page.set_default_timeout(60_000)
            for case_no in cfg.get("params", {}).get("cases", []):
                if not re.fullmatch(r"\d{2}-\d{4}", case_no):
                    raise ValueError("Invalid configured OPSB case number")
                page.goto("https://dis.puc.state.oh.us/", wait_until="domcontentloaded")
                page.locator("#txtCaseLookup").wait_for(state="visible")
                page.locator("#txtCaseLookup").fill(case_no)
                page.locator('#caselookupform input[type="image"]').click()
                page.locator("#ContentPlaceHolderMaster_gvDocketInformation").wait_for(state="visible")
                match = re.search(r"\d+\s*-\s*\d+\s+of\s+(\d+)\s+documents", page.locator("body").inner_text())
                if not match:
                    raise RuntimeError(f"OPSB {case_no}: missing advertised document count")
                expected = int(match[1])
                page.get_by_role("link", name="View All", exact=True).click()
                page.wait_for_function("""count => document.querySelectorAll(
                    '#ContentPlaceHolderMaster_gvDocketInformation a[href*="DocumentRecord.aspx"]'
                    ).length === count""", arg=expected)
                headings = " ".join(page.locator("h1").all_text_contents())
                grid = page.locator("#ContentPlaceHolderMaster_gvDocketInformation").evaluate("table => table.outerHTML")
                rows = validate_opsb_case(headings + "\n" + grid, case_no, expected)
                for row in rows:
                    key = row["external_id"]
                    if key not in all_rows:
                        row["payload"]["case_numbers"] = [case_no]
                        all_rows[key] = row
                    elif case_no not in all_rows[key]["payload"]["case_numbers"]:
                        all_rows[key]["payload"]["case_numbers"].append(case_no)
                latest = max(dt.datetime.strptime(r["payload"]["filed"], "%m/%d/%Y").date() for r in rows)
                print(f"VERIFIED OPSB {case_no}: {len(rows)} documents; latest {latest}", flush=True)
        finally:
            browser.close()
    if not all_rows:
        raise RuntimeError("OPSB: no configured docket documents")
    return list(all_rows.values())


def run_opsb_case(cfg: dict) -> list[dict]:
    if cfg.get("params", {}).get("transport") == "browser":
        return run_opsb_browser(cfg)
    out = []
    for case_no in cfg.get("params", {}).get("cases", []):
        r = httpx.get(cfg["url"], params={"CaseNo": case_no, "link": "DIVA"},
                      headers=BROWSER_HEADERS, timeout=60,
                      follow_redirects=True)
        r.raise_for_status()
        rows = parse_opsb_case_html(r.text, case_no)
        if not rows:
            raise RuntimeError(
                f"OPSB {case_no}: 0 filings parsed — layout change or WAF "
                f"block (status {r.status_code}, {len(r.text)} bytes)")
        out.extend(rows)
    return out


# --------------------------------------------------------------- pagehash --

def normalize_page_text(html: str) -> str:
    """Visible-ish text: scripts/styles out, tags out, whitespace collapsed."""
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html,
                  flags=re.S | re.I)
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    html = re.sub(r"<[^>]+>", " ", html)
    html = html.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", html).strip()


def run_pagehash(cfg: dict) -> list[dict]:
    """One candidate per distinct content version of a page.

    external_id = content hash prefix, so the unique constraint stores each
    version exactly once: first run records the baseline, later runs add a
    candidate only when the page actually changes.
    """
    r = httpx.get(cfg["url"], headers=BROWSER_HEADERS, timeout=60,
                  follow_redirects=True)
    r.raise_for_status()
    text = normalize_page_text(r.text)
    if len(text) < 200:
        raise RuntimeError(f"suspiciously short page ({len(text)} chars) — "
                           f"error page or block")
    h = hashlib.sha256(text.encode()).hexdigest()
    return [{
        "external_id": h[:16],
        "url": cfg["url"],
        "title": f"{cfg['id']}: content version {h[:8]} ({len(text)} chars)",
        "payload": {"hash": h, "chars": len(text), "excerpt": text[:400]},
    }]


# ------------------------------------------------------- ECHO compliance --

ECHO_COUNTER_KEYS = ("QueryRows", "SVRows", "CVRows", "V3Rows", "FEARows",
                     "InfFEARows", "INSPRows", "FCERows", "TotalPenalties")


def echo_counter_candidate(name: str, results: dict, query: dict) -> dict:
    """Pure mapper: ECHO get_facilities summary counters → candidate."""
    counters = {k: results.get(k) for k in ECHO_COUNTER_KEYS}
    h = hashlib.sha256(
        json.dumps(counters, sort_keys=True).encode()).hexdigest()
    return {
        "external_id": f"{name}:{h[:12]}",
        "url": None,
        "title": (f"ECHO {name}: facilities {counters['QueryRows']}, "
                  f"inspections {counters['INSPRows']}, informal EA "
                  f"{counters['InfFEARows']}, formal EA {counters['FEARows']},"
                  f" penalties {counters['TotalPenalties']}"),
        "payload": {"query": query, "counters": counters},
    }


def run_echo_counters(cfg: dict) -> list[dict]:
    """Watch EPA ECHO air-compliance activity counters per named query.

    A new inspection / enforcement action / penalty at a watched operator
    changes the counters → new counter-hash → one candidate. Baseline row on
    first run.
    """
    out = []
    for q in cfg.get("params", {}).get("queries", []):
        params = {"output": "JSON"}
        params.update({k: v for k, v in q.items() if k != "name"})
        r = httpx.get(cfg["url"], params=params, headers=BROWSER_HEADERS,
                      timeout=60)
        r.raise_for_status()
        results = r.json().get("Results", {})
        if "Error" in results:
            raise RuntimeError(f"ECHO {q['name']}: "
                               f"{results['Error'].get('ErrorMessage')}")
        out.append(echo_counter_candidate(q["name"], results, q))
        time.sleep(1)
    return out


# ------------------------------------------------------------ GDELT news --

def gdelt_candidates(payload: dict) -> list[dict]:
    out = []
    for a in payload.get("articles", []):
        url = a.get("url") or ""
        if not url:
            continue
        h = hashlib.sha256(url.encode()).hexdigest()[:16]
        out.append({
            "external_id": h,
            "url": url,
            "title": f"{(a.get('title') or '')[:140]} "
                     f"({a.get('domain')}, {a.get('seendate')})",
            "payload": {"domain": a.get("domain"),
                        "seendate": a.get("seendate"),
                        "title": a.get("title")},
        })
    return out


def run_gdelt(cfg: dict) -> list[dict]:
    p = cfg.get("params", {})
    params = {
        "query": p.get("query", ""),
        "mode": "artlist", "format": "json",
        "timespan": p.get("timespan", "2d"),
        "maxrecords": str(p.get("maxrecords", 40)),
    }
    # GDELT's free DOC API rate-limits by IP, and shared CI egress (GitHub
    # runners) gets 429/503 in bursts. We issue one query per day, so a short
    # backoff almost always clears the throttle before the SLO alarm fires.
    # Retry-After is honored when present; jitter avoids lockstep with other
    # callers on the same runner IP pool.
    backoffs = [5, 15, 40]
    for attempt in range(len(backoffs) + 1):
        r = httpx.get(cfg["url"], params=params, headers=BROWSER_HEADERS,
                      timeout=60)
        if r.status_code in (429, 503) and attempt < len(backoffs):
            ra = r.headers.get("Retry-After")
            try:
                wait = float(ra) if ra else backoffs[attempt]
            except ValueError:
                wait = backoffs[attempt]
            print(f"gdelt: HTTP {r.status_code}, retry in ~{wait:.0f}s "
                  f"(attempt {attempt + 1}/{len(backoffs)})")
            time.sleep(wait + random.uniform(0, 3))
            continue
        r.raise_for_status()
        try:
            payload = r.json()
        except ValueError as e:
            raise RuntimeError(f"GDELT non-JSON reply: {r.text[:120]}") from e
        return gdelt_candidates(payload)


# ------------------------------------------------- Sentinel-2 (Island Watch) --

def stac_scene_candidates(site: str, payload: dict,
                          max_cloud: float) -> list[dict]:
    """Pure mapper: Earth Search items → one candidate per usable scene."""
    out = []
    for f in payload.get("features", []):
        props = f.get("properties", {})
        cc = props.get("eo:cloud_cover")
        if cc is not None and cc > max_cloud:
            continue
        thumb = (f.get("assets", {}).get("thumbnail") or {}).get("href")
        dt_str = (props.get("datetime") or "")[:10]
        out.append({
            "external_id": f"{site}:{f.get('id')}",
            "url": thumb,
            "title": f"S2 scene over {site}: {dt_str}, "
                     f"cloud {round(cc, 1) if cc is not None else '?'}%",
            "payload": {"site": site, "scene_id": f.get("id"),
                        "datetime": props.get("datetime"),
                        "cloud_cover": cc, "preview": thumb},
        })
    return out


def run_sentinel_stac(cfg: dict) -> list[dict]:
    """New Sentinel-2 L2A scenes over watched site AOIs (Earth Search, keyless).

    v1 is scene-availability + preview: each new usable scene becomes a
    candidate the reviewer can eyeball from the review PR. Spectral change
    detection over the AOI (windowed COG reads) is the planned v1.1 upgrade.
    """
    p = cfg.get("params", {})
    window = int(p.get("window_days", 12))
    max_cloud = float(p.get("max_cloud", 60))
    since = (dt.datetime.now(dt.timezone.utc)
             - dt.timedelta(days=window)).strftime("%Y-%m-%dT00:00:00Z")
    out = []
    for site in p.get("sites", []):
        d = 0.01
        bbox = (f"{site['lon']-d},{site['lat']-d},"
                f"{site['lon']+d},{site['lat']+d}")
        r = httpx.get(cfg["url"], params={
            "bbox": bbox, "limit": "10",
            "datetime": f"{since}/.."},
            headers=BROWSER_HEADERS, timeout=60)
        r.raise_for_status()
        out.extend(stac_scene_candidates(site["name"], r.json(), max_cloud))
        time.sleep(1)
    return out


# --------------------------------------------------------- CourtListener --

def courtlistener_candidates(party: str, payload: dict) -> list[dict]:
    out = []
    for res in payload.get("results", [])[:10]:
        did = res.get("docket_id") or res.get("id")
        out.append({
            "external_id": f"cl-{did}",
            "url": "https://www.courtlistener.com"
                   + (res.get("docket_absolute_url")
                      or res.get("absolute_url") or ""),
            "title": f"{party}: {res.get('caseName')} "
                     f"({res.get('court')}, filed {res.get('dateFiled')})",
            "payload": {"party": party, "docket_id": did,
                        "case_name": res.get("caseName"),
                        "court": res.get("court"),
                        "date_filed": res.get("dateFiled")},
        })
    return out


def run_courtlistener(cfg: dict) -> list[dict]:
    headers = dict(BROWSER_HEADERS)
    headers["Accept"] = "application/json"
    token = os.environ.get("COURTLISTENER_TOKEN")
    if token:  # anonymous access is throttled/blocked from datacenter IPs
        headers["Authorization"] = f"Token {token.strip()}"
    else:
        print("courtlistener: COURTLISTENER_TOKEN not set — expect blocks")
    out = []
    for party in cfg.get("params", {}).get("parties", []):
        r = httpx.get(cfg["url"], params={
            "q": f'"{party}"', "type": "r",
            "order_by": "dateFiled desc"},
            headers=headers, timeout=60, follow_redirects=True)
        r.raise_for_status()
        try:
            data = r.json()
        except ValueError:
            # 200 with an HTML body = block/challenge page. Surface what
            # came back so the failure is diagnosable from the run log.
            ct = r.headers.get("content-type", "?")
            raise RuntimeError(
                f"non-JSON response for {party!r}: HTTP {r.status_code}, "
                f"content-type {ct}, body starts "
                f"{r.text[:120]!r}") from None
        out.extend(courtlistener_candidates(party, data))
        time.sleep(1)
    return out


# ----------------------------------------------------------- FERC eLibrary --

def ferc_candidates(payload: dict, kws: list[str]) -> list[dict]:
    """Pure mapper: AdvancedSearch hits → candidates. NB: the API really
    does spell it 'acesssionNumber' (probe 2026-07-13)."""
    out = []
    for hit in payload.get("searchHits", []):
        desc = re.sub(r"\s+", " ", (hit.get("description") or "")).strip()
        acc = (hit.get("acesssionNumber") or hit.get("accessionNumber")
               or hit.get("documentId"))
        dockets = [d for d in (hit.get("docketNumbers") or []) if d]
        blob = f"{desc} {' '.join(dockets)}".lower()
        out.append({
            "external_id": f"ferc-{acc}",
            "url": ("https://elibrary.ferc.gov/eLibrary/filelist"
                    f"?accession_number={acc}"),
            "title": f"FERC {dockets[0] if dockets else '—'} "
                     f"{hit.get('filedDate', '')}: {desc[:140]}",
            "payload": {"accession": acc, "dockets": dockets,
                        "filed": hit.get("filedDate"),
                        "category": hit.get("category"),
                        "description": desc,
                        "kw_hit": any(k in blob for k in kws)},
        })
    return out


def run_ferc_search(cfg: dict) -> list[dict]:
    """FERC eLibrary keyword watcher (undocumented JSON API, probe
    2026-07-13): POST eLibraryWebAPI/api/Search/AdvancedSearch — keyless,
    description search over a trailing filed-date window."""
    p = cfg.get("params", {})
    today = dt.date.today()
    frm = today - dt.timedelta(days=int(p.get("window_days", 7)))
    body = {
        "searchText": p.get("query", ""),
        "searchFullText": bool(p.get("full_text", False)),
        "searchDescription": True,
        "dateSearches": [{"dateType": "filed_date",
                          "startDate": frm.isoformat(),
                          "endDate": today.isoformat()}],
        "availability": None, "affiliations": [], "categories": [],
        "libraries": [], "accessionNumber": None, "eFiling": False,
        "docketSearches": [{"docketNumber": "", "subDocketNumbers": []}],
        "resultsPerPage": int(p.get("max_results", 100)), "curPage": 1,
        "classTypes": [], "sortBy": "", "groupBy": "NONE",
        "idolResultID": "", "allDates": False,
    }
    headers = dict(BROWSER_HEADERS)
    headers["Accept"] = "application/json"
    r = httpx.post(cfg["url"], json=body, headers=headers, timeout=60)
    r.raise_for_status()
    payload = r.json()
    if not payload.get("success", True):
        raise RuntimeError(f"FERC search error: "
                           f"{payload.get('errorMessage')}")
    kws = [k.lower() for k in cfg.get("keywords", [])]
    return ferc_candidates(payload, kws)


# ---------------------------------------------- MDEQ draft-permit notices --

_MDEQ_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_MDEQ_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)


def parse_mdeq_notice(html: str) -> list[dict]:
    """Pure parser: enSearch 'EPD Permits at Public Notice' GridView →
    one dict per draft permit row (server-rendered aspx, no JS)."""
    out = []
    for row_html in _MDEQ_ROW.findall(html):
        cells = [re.sub(r"\s+", " ",
                        re.sub(r"<[^>]+>", " ",
                               c.replace("&amp;", "&"))).strip()
                 for c in _MDEQ_CELL.findall(row_html)]
        if len(cells) < 5 or cells[0] in ("", "Name"):
            continue
        out.append({"name": cells[0], "permit_type": cells[1],
                    "city": cells[2], "county": cells[3],
                    "end_date": cells[4]})
    return out


def mdeq_candidates(rows: list[dict], kws: list[str],
                    page_url: str) -> list[dict]:
    """Pure mapper: one candidate per (name, permit type, notice end date).
    Idempotent across daily reruns via the content-derived external_id."""
    out = []
    for row in rows:
        key = f"{row['name']}|{row['permit_type']}|{row['end_date']}"
        h = hashlib.sha256(key.encode()).hexdigest()[:16]
        blob = " ".join(row.values()).lower()
        out.append({
            "external_id": f"mdeq-{h}",
            "url": page_url,
            "title": f"MDEQ draft permit: {row['name']} — "
                     f"{row['permit_type']} ({row['county']}, "
                     f"notice ends {row['end_date']})",
            "payload": {**row, "kw_hit": any(k in blob for k in kws)},
        })
    return out


def run_mdeq_notice(cfg: dict) -> list[dict]:
    r = httpx.get(cfg["url"], headers=BROWSER_HEADERS, timeout=60,
                  follow_redirects=True)
    r.raise_for_status()
    rows = parse_mdeq_notice(r.text)
    if not rows:
        raise RuntimeError(f"MDEQ notice page: 0 rows parsed — layout "
                           f"change? ({len(r.text)} bytes)")
    kws = [k.lower() for k in cfg.get("keywords", [])]
    return mdeq_candidates(rows, kws, cfg["url"])


# ----------------------------------------------- Gas EBB (Island Watch) --

def parse_gas_ebb_csv(text: str, needles: list[str]) -> list[dict]:
    """Pure parser: NAESB operationally-available CSV → rows whose Loc Name
    contains any needle (case-insensitive). Handles quoted commas and the
    UTF-8 BOM."""
    import csv as _csv
    import io as _io
    out = []
    for row in _csv.DictReader(_io.StringIO(text.lstrip("﻿"))):
        name = row.get("Loc Name") or ""
        if any(n.lower() in name.lower() for n in needles):
            out.append(row)
    return out


def gas_ebb_candidates(rows: list[dict], cycle: str, doc_url: str) -> list[dict]:
    """Pure mapper: matched CSV rows → candidates. One candidate per
    (location, gas day): dedupe by external_id keeps the first capture of
    the day; later cycles are ignored. Zero scheduled flow at a watched
    island IS the signal → kw_hit so it surfaces in the review PR."""
    out = []
    for row in rows:
        gas_day = (row.get("Effective Gas Day") or "").strip()
        loc = (row.get("Loc") or "").strip()
        sched_raw = (row.get("Total Scheduled Quantity") or "").replace(",", "").strip()
        try:
            sched = float(sched_raw) if sched_raw else 0.0
        except ValueError:
            sched = 0.0
        out.append({
            "external_id": f"tgt-{loc}-{gas_day}",
            "url": doc_url,
            "title": f"Gas pulse {row.get('Loc Name')}: "
                     f"{sched_raw or '0'} MMBtu scheduled, "
                     f"gas day {gas_day} ({cycle})",
            "payload": {"loc": loc, "loc_name": row.get("Loc Name"),
                        "gas_day": gas_day, "cycle": cycle,
                        "scheduled_mmbtu": sched,
                        "kw_hit": sched == 0.0,  # silence = the anomaly
                        "row": row},
        })
    return out


def run_gas_ebb(cfg: dict) -> list[dict]:
    """Daily gas pulse from a GasQuest-style EBB (probe 2026-07-13):
    newest Operational Capacity posting → its CSV (served base64) → rows for
    watched delivery locations. The continuous \"is it running\" signal
    between satellite snapshots (docs/island-watch/gas-recon-2026-07.md)."""
    import base64
    p = cfg.get("params", {})
    base = cfg["url"].rstrip("/")
    body = {"infoPostID": int(p.get("info_post_id", 1)),
            "tspId": int(p.get("tsp_id", 100000)),
            "pageNumber": 1, "pageSize": 1,
            "sortBy": "datetimePostingEffective",
            "groupCode": "INFOPOST", "sortDescending": True}
    r = httpx.post(base + "/infopostdetails", json=body,
                   headers=BROWSER_HEADERS, timeout=60)
    r.raise_for_status()
    postings = r.json().get("postings", [])
    if not postings:
        raise RuntimeError("gas-ebb: no postings returned")
    posting = postings[0]
    csv_files = [f for f in posting.get("reportFiles", [])
                 if "CSV" in (f.get("infoPostDocumentTypeTitle") or "")]
    if not csv_files:
        raise RuntimeError("gas-ebb: newest posting carries no CSV file")
    doc_id = csv_files[0]["infoPostTrackerID"]
    doc_url = f"{base}/postings?postingsDocumentId={doc_id}"
    r2 = httpx.get(doc_url, headers=BROWSER_HEADERS, timeout=60)
    r2.raise_for_status()
    try:
        text = base64.b64decode(r2.text, validate=True).decode("utf-8-sig")
    except Exception:  # noqa: BLE001 — tolerate a switch to plain CSV
        text = r2.text
    rows = parse_gas_ebb_csv(text, p.get("locations", ["MZX"]))
    return gas_ebb_candidates(rows, posting.get("cycleCode", ""), doc_url)


ADAPTERS = {
    "tceq_nsr": run_tceq_nsr,
    "tceq_portal": run_tceq_portal,
    "opsb_case": run_opsb_case,
    "pagehash": run_pagehash,
    "echo_counters": run_echo_counters,
    "gdelt_news": run_gdelt,
    "courtlistener": run_courtlistener,
    "sentinel_stac": run_sentinel_stac,
    "gas_ebb": run_gas_ebb,
    "ferc_search": run_ferc_search,
    "mdeq_notice": run_mdeq_notice,
}


# ----------------------------------------------------------------- runtime --

def upsert_source(cfg: dict) -> None:
    _rest("POST", "source", params={"on_conflict": "id"},
          headers={"Prefer": "resolution=merge-duplicates"},
          json=[{
              "id": cfg["id"], "kind": cfg["kind"], "url": cfg["url"],
              "adapter": cfg["adapter"], "schedule": cfg.get("schedule", "daily"),
              "slo_interval": f"{cfg.get('slo_interval_days', 14)} days",
          }])


def _count(source_id: str) -> int:
    r = _rest("GET", "candidate", params={
        "select": "id", "source_id": f"eq.{source_id}"},
        headers={"Prefer": "count=exact", "Range": "0-0"})
    return int(r.headers.get("content-range", "*/0").split("/")[1])


def store_candidates(source_id: str, cands: list[dict]) -> int:
    if not cands:
        return 0
    before = _count(source_id)
    rows = [{"source_id": source_id, **c} for c in cands]
    _rest("POST", "candidate",
          params={"on_conflict": "source_id,external_id"},
          headers={"Prefer": "resolution=ignore-duplicates"},
          json=rows)
    return _count(source_id) - before


def touch_source(source_id: str) -> None:
    _rest("PATCH", "source", params={"id": f"eq.{source_id}"},
          json={"last_hit_at": dt.datetime.now(dt.timezone.utc).isoformat()})


def slo_check() -> int:
    """Exit non-zero if any live source hasn't succeeded within its SLO.

    A source that has never succeeded gets a grace window of one SLO interval
    from its creation — new/blocked adapters surface as warnings first and as
    alarms only once they've been failing for the whole interval.
    """
    stale = []
    now = dt.datetime.now(dt.timezone.utc)
    for cfg in load_sources():
        rows = _rest("GET", "source", params={
            "select": "id,last_hit_at,created_at",
            "id": f"eq.{cfg['id']}"}).json()
        max_age = dt.timedelta(days=int(cfg.get("slo_interval_days", 14)))
        if not rows:
            print(f"WARN {cfg['id']}: no source row yet")
            continue
        row = rows[0]
        if not row["last_hit_at"]:
            born = dt.datetime.fromisoformat(row["created_at"])
            if now - born > max_age:
                stale.append(f"{cfg['id']}: never succeeded, "
                             f"created {(now - born).days}d ago")
            else:
                print(f"WARN {cfg['id']}: never succeeded (grace window)")
            continue
        age = now - dt.datetime.fromisoformat(row["last_hit_at"])
        if age > max_age:
            stale.append(f"{cfg['id']}: last success {age.days}d ago "
                         f"(SLO {max_age.days}d)")
    for s in stale:
        print("STALE", s)
    return 1 if stale else 0


def heartbeat() -> None:
    url = os.environ.get("HEARTBEAT_URL")
    if url:
        try:
            httpx.get(url, timeout=10)
        except Exception as e:  # noqa: BLE001
            print(f"heartbeat ping failed: {e}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", help="run only this source id")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--slo", action="store_true",
                    help="staleness check only; exit 1 if any source stale")
    args = ap.parse_args()

    if args.slo:
        sys.exit(slo_check())

    if args.source and not any(c["id"] == args.source for c in load_sources()):
        ap.error("source is unknown or retired")

    failures = 0
    for cfg in load_sources():
        if args.source and cfg["id"] != args.source:
            continue
        adapter = cfg.get("adapter")
        fn = ADAPTERS.get(adapter) if adapter else None
        if fn is None:  # missing or unknown adapter must not kill the sweep
            print(f"SKIP {cfg['id']}: unknown adapter {adapter!r}")
            continue
        if not args.dry_run:
            upsert_source(cfg)  # track the source even if today's run fails
        try:
            cands = fn(cfg)
            if args.dry_run:
                print(f"DRY  {cfg['id']}: {len(cands)} candidates")
                for c in cands[:5]:
                    print("     ", c["external_id"], c["title"][:100])
                continue
            new = store_candidates(cfg["id"], cands)
            touch_source(cfg["id"])
            print(f"OK   {cfg['id']}: {len(cands)} rows, {new} new")
        except Exception as e:  # noqa: BLE001 — one bad source must not kill the rest
            failures += 1
            print(f"FAIL {cfg['id']}: {e}")

    # Individual source failures do NOT fail the run — the SLO check is the
    # alarm (grace window, then red). A run with zero successes still fails.
    if not args.dry_run and failures:
        print(f"{failures} source(s) failed (SLO check is the alarm)")
    if not args.dry_run and not args.source:
        heartbeat()
    if args.source and failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
