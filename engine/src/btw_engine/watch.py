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

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY; optional HEARTBEAT_URL.
"""

import argparse
import datetime as dt
import glob
import os
import re
import sys

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
    """Parse the NSR search's ASCII (pipe-delimited) output."""
    text = re.sub(r"<[^>]+>", "", text)  # tolerate HTML wrapping
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("NSR|"):
            continue
        parts = [p.strip() for p in line.split("|")]
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


ADAPTERS = {
    "tceq_nsr": run_tceq_nsr,
    "opsb_case": run_opsb_case,
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
    """Exit non-zero if any live source hasn't succeeded within its SLO."""
    stale = []
    for cfg in load_sources():
        rows = _rest("GET", "source", params={
            "select": "id,last_hit_at",
            "id": f"eq.{cfg['id']}"}).json()
        if not rows or not rows[0]["last_hit_at"]:
            stale.append(f"{cfg['id']}: never succeeded")
            continue
        last = dt.datetime.fromisoformat(rows[0]["last_hit_at"])
        max_age = dt.timedelta(days=int(cfg.get("slo_interval_days", 14)))
        age = dt.datetime.now(dt.timezone.utc) - last
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
        try:
            cands = fn(cfg)
            if args.dry_run:
                print(f"DRY  {cfg['id']}: {len(cands)} candidates")
                for c in cands[:5]:
                    print("     ", c["external_id"], c["title"][:100])
                continue
            upsert_source(cfg)
            new = store_candidates(cfg["id"], cands)
            touch_source(cfg["id"])
            print(f"OK   {cfg['id']}: {len(cands)} rows, {new} new")
        except Exception as e:  # noqa: BLE001 — one bad source must not kill the rest
            failures += 1
            print(f"FAIL {cfg['id']}: {e}")

    if not args.dry_run and not failures:
        heartbeat()
    if failures:
        sys.exit(f"{failures} source(s) failed")


if __name__ == "__main__":
    main()
