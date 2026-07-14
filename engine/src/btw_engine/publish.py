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

from btw_engine.truth import assert_provenance
from btw_engine.public_archive import archive_url, load_manifest

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
               "unit(id,oem,model,unit_count,mw_each,total_mw,fuel,"
               "hours_permitted,basis,verification_state),"
               "permit(id,authority,permit_no,permit_type,status,filed_at,"
               "issued_at,verification_state)",
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
    announcements = fetch_announcements()
    return facilities, events, aggregates, announcements


def fetch_announcements() -> list[dict]:
    """Fetch the third-party pipeline independently of verified fleet facts."""
    return get(
        "announcement",
        select="name,state,county,project_type,operating_status,"
               "expected_operating_year,generating_technology,"
               "reported_capacity_mw,source_as_of,"
               "source_document:document!announcement_source_document_id_fkey(url,doc_genre)",
        fact_state="eq.published",
        order="reported_capacity_mw.desc.nullslast,name",
    )


def fetch_provenance() -> list[dict]:
    """The receipt for every fact: fact_provenance -> claim -> document."""
    return get("fact_provenance",
               select="fact_table,fact_id,fact_field,note,support_kind,"
                      "derivation,claim(field,value,value_num,status,anchor,"
                      "quote,page,bbox,match_score,numeric_check,"
                      "document(url,r2_key,sha256,fetched_at,doc_genre))")


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

    public_manifest = load_manifest()
    for row in prov:
        claim = row.get("claim") or {}
        doc = claim.get("document") or {}
        url = doc.get("url")
        f = owner.get((row["fact_table"], row["fact_id"]))
        if not url or f is None:
            continue
        srcs = f.setdefault("sources", [])
        sha256 = doc.get("sha256")
        # A publisher may replace a file at the same URL. Treat the immutable
        # content hash as the document identity so both captures remain visible.
        hit = next((s for s in srcs if (
            sha256 and s.get("sha256") == sha256
        ) or (not sha256 and s["url"] == url)), None)
        if hit is None:
            archived_copy = archive_url(
                sha256, doc.get("r2_key"), manifest=public_manifest)
            hit = {
                "url": url,
                "archive_url": archived_copy,
                "sha256": sha256,
                "archived_at": doc.get("fetched_at"),
                "archive_status": (
                    "public" if archived_copy else
                    "approved_pending_endpoint" if sha256 in public_manifest else
                    "preserved_private"),
                "doc_genre": doc.get("doc_genre"),
                "facts": [],
            }
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
    return sum(float(u["total_mw"])
               if u.get("total_mw") is not None
               else (u.get("unit_count") or 0) * float(u.get("mw_each") or 0)
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
        "method": "sum(coalesce(total_mw, unit_count*mw_each)) over facilities "
                  "with status=operating; basis and derivation are retained "
                  "per unit cohort",
        "license": "CC BY 4.0",
        "cite_as": CITE,
    }


def announcement_summary(announcements: list[dict]) -> dict:
    known = [a for a in announcements if a.get("reported_capacity_mw") is not None]
    total_mw = sum(float(a["reported_capacity_mw"]) for a in known)
    statuses: dict[str, dict] = {}
    states: dict[str, float] = {}
    for a in announcements:
        mw = float(a.get("reported_capacity_mw") or 0)
        status = a.get("operating_status") or "Unknown"
        bucket = statuses.setdefault(status, {"projects": 0, "reported_mw": 0.0})
        bucket["projects"] += 1
        bucket["reported_mw"] += mw
        if a.get("state"):
            states[a["state"]] = states.get(a["state"], 0.0) + mw
    return {
        "projects": len(announcements),
        "projects_with_capacity": len(known),
        "reported_gw": round(total_mw / 1000, 1),
        "statuses": statuses,
        "top_states": [
            {"state": state, "reported_gw": round(mw / 1000, 1)}
            for state, mw in sorted(states.items(), key=lambda item: item[1], reverse=True)[:6]
        ],
    }


def write_announcements(out: str, announcements: list[dict]) -> None:
    """Write only the separate, third-party announcement evidence layer."""
    os.makedirs(out, exist_ok=True)
    rows = []
    for source_row in announcements:
        row = dict(source_row)
        document = row.pop("source_document", None) or {}
        row["source"] = {
            "url": document.get("url"),
            "doc_genre": document.get("doc_genre"),
        }
        rows.append(row)
    with open(f"{out}/announcements.json", "w") as fp:
        json.dump({
            "license": "CC BY 4.0",
            "cite_as": CITE,
            "classification": "third_party_reported_not_btw_verified",
            "note": "Reported project pipeline from a cited third-party inventory. "
                    "Do not add to BTW verified operating capacity.",
            "summary": announcement_summary(rows),
            "announcements": rows,
        }, fp, indent=2, default=str)


def write_files(out: str, facilities, events, summary, coverage=None,
                announcements=None):
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

    if announcements is not None:
        write_announcements(out, announcements)

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
    ap.add_argument(
        "--announcements-only", action="store_true",
        help="publish the separate third-party announcement layer only",
    )
    args = ap.parse_args()

    if args.announcements_only:
        announcements = fetch_announcements()
        write_announcements(args.out, announcements)
        summary = announcement_summary(announcements)
        print(f"published {len(announcements)} third-party announcements, "
              f"reported_gw={summary['reported_gw']} -> {args.out}/")
        return

    facilities, events, aggregates, announcements = fetch()
    provenance = fetch_provenance()
    assert_provenance(facilities, provenance, gate="PUBLISH TRUTH GATE")
    attach_sources(facilities, provenance)
    coverage = fetch_coverage()
    summary = build_summary(facilities, aggregates)
    write_files(args.out, facilities, events, summary, coverage, announcements)
    print(f"published {len(facilities)} facilities, {len(events)} events, "
          f"{len(announcements)} third-party announcements, "
          f"{len(coverage)} sources in coverage, "
          f"operating_gw={summary['operating_gw']} -> {args.out}/")


if __name__ == "__main__":
    main()
