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
import re
import sys
import time

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
        if cfg.get("status") == "stub":
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
    kws = [k.lower() for k in cfg.get("keywords", [])]
    out = []
    for row in parse_tceq_ascii(r.text):
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


# --------------------------------------------------------------- OPSB case --

_OPSB_ROW = re.compile(
    r'href="(DocumentRecord\.aspx\?DocID=([0-9a-fA-F-]+))">'
    r"(\d{2}/\d{2}/\d{4})</a></td><td>(.*?)</td>", re.S)


def parse_opsb_case_html(html: str, case_no: str) -> list[dict]:
    """Parse the filings grid on CaseRecord.aspx (first page = newest 15)."""
    out = []
    for m in _OPSB_ROW.finditer(html):
        rel_url, doc_id, filed, summary = m.groups()
        summary = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", summary)).strip()
        out.append({
            "external_id": doc_id,
            "url": "https://dis.puc.state.oh.us/" + rel_url,
            "title": f"{case_no} {filed}: {summary[:160]}",
            "payload": {"case_no": case_no, "filed": filed,
                        "summary": summary},
        })
    return out


def run_opsb_case(cfg: dict) -> list[dict]:
    out = []
    for case_no in cfg.get("params", {}).get("cases", []):
        r = httpx.get(cfg["url"], params={"CaseNo": case_no},
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
    r = httpx.get(cfg["url"], params={
        "query": p.get("query", ""),
        "mode": "artlist", "format": "json",
        "timespan": p.get("timespan", "2d"),
        "maxrecords": str(p.get("maxrecords", 40)),
    }, headers=BROWSER_HEADERS, timeout=60)
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
    token = os.environ.get("COURTLISTENER_TOKEN")
    if token:  # anonymous access is throttled/blocked from datacenter IPs
        headers["Authorization"] = f"Token {token}"
    out = []
    for party in cfg.get("params", {}).get("parties", []):
        r = httpx.get(cfg["url"], params={
            "q": f'"{party}"', "type": "r",
            "order_by": "dateFiled desc"},
            headers=headers, timeout=60)
        r.raise_for_status()
        out.extend(courtlistener_candidates(party, r.json()))
        time.sleep(1)
    return out


ADAPTERS = {
    "tceq_nsr": run_tceq_nsr,
    "opsb_case": run_opsb_case,
    "pagehash": run_pagehash,
    "echo_counters": run_echo_counters,
    "gdelt_news": run_gdelt,
    "courtlistener": run_courtlistener,
    "sentinel_stac": run_sentinel_stac,
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

    failures = 0
    for cfg in load_sources():
        if args.source and cfg["id"] != args.source:
            continue
        fn = ADAPTERS.get(cfg["adapter"])
        if fn is None:
            print(f"SKIP {cfg['id']}: unknown adapter {cfg['adapter']}")
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
    if not args.dry_run:
        heartbeat()


if __name__ == "__main__":
    main()
