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
        select="slug,name,aliases,state,county,developer,offtaker,status,flags,"
               "first_permit_filed,first_power,unit(oem,model,unit_count,mw_each,hours_permitted),"
               "permit(authority,permit_no,permit_type,status,filed_at,issued_at)",
        fact_state="eq.published",
        order="slug",
        # embedded resources need their own filters or staging rows leak in
        **{"unit.fact_state": "eq.published",
           "permit.fact_state": "eq.published"},
    )
    events = get(
        "event",
        select="event_date,event_type,headline,facility(slug)",
        fact_state="eq.published",
        order="event_date.desc",
    )
    aggregates = get("aggregate", select="metric,value,method,inputs_note,computed_at",
                     order="computed_at.desc")
    return facilities, events, aggregates


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


def write_files(out: str, facilities, events, summary):
    os.makedirs(out, exist_ok=True)

    with open(f"{out}/facilities.json", "w") as fp:
        json.dump({"license": "CC BY 4.0", "cite_as": CITE,
                   "facilities": facilities}, fp, indent=2, default=str)

    with open(f"{out}/events.json", "w") as fp:
        json.dump({"license": "CC BY 4.0", "cite_as": CITE,
                   "events": [
                       {"date": e["event_date"], "type": e["event_type"],
                        "headline": e["headline"],
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
    summary = build_summary(facilities, aggregates)
    write_files(args.out, facilities, events, summary)
    print(f"published {len(facilities)} facilities, {len(events)} events, "
          f"operating_gw={summary['operating_gw']} -> {args.out}/")


if __name__ == "__main__":
    main()
