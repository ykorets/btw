"""btw_engine.publish — Postgres → mirror files (architecture §4.8, plan M1).

Reads published facts via Supabase PostgREST (service key), writes the
canonical file set. Fails hard if recomputed aggregates disagree with the
stored aggregate row (consistency gate, architecture §2).

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY.
Usage: python -m btw_engine.publish --out out/
"""

import argparse
import csv
import json
import os
import sys
from datetime import date

import httpx

BASE = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"
KEY = os.environ["SUPABASE_SERVICE_KEY"]
HEADERS = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}

CITE = "Behind the Watt, behindthewatt.com, CC BY 4.0"


def get(table: str, **params) -> list[dict]:
    r = httpx.get(f"{BASE}/{table}", headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch():
    facilities = get(
        "facility",
        select="id,slug,name,aliases,state,county,geo,developer,offtaker,status,flags,"
               "first_permit_filed,first_power,"
               "unit(id,oem,model,unit_count,mw_each,hours_permitted),"
               "permit(id,authority,permit_no,permit_type,status,filed_at,issued_at)",
        fact_state="eq.published",
        order="slug",
        # embedded resources need their own filters or staging rows leak in
        **{"unit.fact_state": "eq.published",
           "permit.fact_state": "eq.published"},
    )
    events = get(
        "event",
        select="event_date,event_type,headline,source_url,facility(slug)",
        fact_state="eq.published",
        order="event_date.desc",
    )
    aggregates = get("aggregate", select="metric,value,method,inputs_note,computed_at",
                     order="computed_at.desc")
    return facilities, events, aggregates


def fetch_provenance() -> list[dict]:
    """The receipt for every fact: fact_provenance -> claim -> document."""
    return get("fact_provenance",
               select="fact_table,fact_id,fact_field,note,"
                      "claim(quote,page,document(url,doc_genre))")


def attach_sources(facilities: list[dict], prov: list[dict]) -> None:
    """Fold provenance rows into each facility as a deduped sources[] list.

    Every published number on the site must be inspectable: for each source
    document we keep the URL, genre, which facts it supports, and the first
    anchored quote. Internal ids are stripped afterwards so the mirror
    schema stays stable.
    """
    owner: dict = {}
    for f in facilities:
        owner[("facility", f["id"])] = f
        for u in f.get("unit", []):
            owner[("unit", u["id"])] = f
        for p in f.get("permit", []):
            owner[("permit", p["id"])] = f

    for row in prov:
        claim = row.get("claim") or {}
        doc = claim.get("document") or {}
        url = doc.get("url")
        f = owner.get((row["fact_table"], row["fact_id"]))
        if not url or f is None:
            continue
        srcs = f.setdefault("sources", [])
        hit = next((s for s in srcs if s["url"] == url), None)
        if hit is None:
            hit = {"url": url, "doc_genre": doc.get("doc_genre"), "facts": []}
            srcs.append(hit)
        label = f"{row['fact_table']}.{row['fact_field']}"
        if label not in hit["facts"]:
            hit["facts"].append(label)
        if claim.get("quote") and "quote" not in hit:
            hit["quote"] = claim["quote"][:280]
            if claim.get("page"):
                hit["page"] = claim["page"]

    for f in facilities:
        f.pop("id", None)
        for u in f.get("unit", []):
            u.pop("id", None)
        for p in f.get("permit", []):
            p.pop("id", None)


def fetch_coverage() -> list[dict]:
    """Which sources are watched since when — the M6 coverage artifact."""
    sources = get("source",
                  select="id,kind,adapter,url,schedule,slo_interval,"
                         "last_hit_at,created_at",
                  order="id")
    for s in sources:
        r = httpx.get(f"{BASE}/candidate",
                      headers={**HEADERS, "Prefer": "count=exact",
                               "Range": "0-0"},
                      params={"select": "id", "source_id": f"eq.{s['id']}"},
                      timeout=30)
        r.raise_for_status()
        s["candidates"] = int(
            r.headers.get("content-range", "*/0").split("/")[1])
    return sources


def facility_mw(f: dict) -> float:
    return sum((u.get("unit_count") or 0) * float(u.get("mw_each") or 0)
               for u in f.get("unit", []))


def build_summary(facilities: list[dict], aggregates: list[dict]) -> dict:
    operating = [f for f in facilities if f["status"] == "operating"]
    operating_gw = round(sum(facility_mw(f) for f in operating) / 1000, 2)

    stored = next((a for a in aggregates if a["metric"] == "operating_gw"), None)
    if stored is not None and abs(float(stored["value"]) - operating_gw) > 0.005:
        sys.exit(
            f"CONSISTENCY GATE: recomputed operating_gw={operating_gw} "
            f"!= stored aggregate {stored['value']} — refusing to publish."
        )

    return {
        "as_of": date.today().isoformat(),
        "operating_gw": operating_gw,
        "operating_facilities": len(operating),
        "facilities_tracked": len(facilities),
        "method": "sum(unit_count*mw_each) over facilities with status=operating; "
                  "unit ratings from permits/observations, per-facility method in provenance",
        "license": "CC BY 4.0",
        "cite_as": CITE,
    }


def write_files(out: str, facilities, events, summary, coverage=None):
    os.makedirs(out, exist_ok=True)

    if coverage is not None:
        with open(f"{out}/coverage.json", "w") as fp:
            json.dump({"license": "CC BY 4.0", "cite_as": CITE,
                       "generated": date.today().isoformat(),
                       "note": "registries watched by the engine; last_hit_at"
                               " = last successful sweep",
                       "sources": coverage}, fp, indent=2, default=str)

    with open(f"{out}/facilities.json", "w") as fp:
        json.dump({"license": "CC BY 4.0", "cite_as": CITE,
                   "facilities": facilities}, fp, indent=2, default=str)

    with open(f"{out}/events.json", "w") as fp:
        json.dump({"license": "CC BY 4.0", "cite_as": CITE,
                   "events": [
                       {"date": e["event_date"], "type": e["event_type"],
                        "headline": e["headline"],
                        "source_url": e.get("source_url"),
                        "facility": (e.get("facility") or {}).get("slug")}
                       for e in events]}, fp, indent=2, default=str)

    with open(f"{out}/summary.json", "w") as fp:
        json.dump(summary, fp, indent=2)

    with open(f"{out}/fleet.csv", "w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["slug", "name", "state", "status", "flags", "mw",
                    "units", "permits", "first_permit_filed", "first_power"])
        for f in facilities:
            w.writerow([
                f["slug"], f["name"], f["state"], f["status"],
                "|".join(f.get("flags") or []),
                round(facility_mw(f), 1),
                "; ".join(f"{u.get('unit_count')}x {u.get('oem')} {u.get('model') or ''}".strip()
                          for u in f.get("unit", [])),
                "; ".join(f"{p['authority']} {p['permit_no']} ({p['status']})"
                          for p in f.get("permit", [])),
                f.get("first_permit_filed") or "",
                f.get("first_power") or "",
            ])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    args = ap.parse_args()

    facilities, events, aggregates = fetch()
    attach_sources(facilities, fetch_provenance())
    coverage = fetch_coverage()
    summary = build_summary(facilities, aggregates)
    write_files(args.out, facilities, events, summary, coverage)
    print(f"published {len(facilities)} facilities, {len(events)} events, "
          f"{len(coverage)} sources in coverage, "
          f"operating_gw={summary['operating_gw']} -> {args.out}/")


if __name__ == "__main__":
    main()
