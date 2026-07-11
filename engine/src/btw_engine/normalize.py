"""btw_engine.normalize — validated claims → provenance + staged updates (M5).

Deterministic only; nothing is invented:
- A document resolves to a facility iff one of its validated permit.no claims
  matches a known permit (non-alphanumerics stripped on both sides).
- A numeric unit claim (count / mw_each / hours_permitted) binds to a unit iff
  the unit's model token appears verbatim in the claim's own quote.
- Bound claim, value == published  → corroboration: fact_provenance link
  (this replaces the M1 manual provenance stubs with anchored claims).
- Bound claim, value != published  → staged update: a fact_state='staging'
  copy of the unit carrying the new value, with provenance rows per changed
  field. The review PR shows the diff; review.py --promote flips it live.
- Unresolved documents and unbound claims are reported, never guessed.

Usage: python -m btw_engine.normalize [--dry-run]
Env: SUPABASE_URL, SUPABASE_SERVICE_KEY.
"""

import argparse
import os
import re

import httpx

FIELD_COLS = {
    "unit.count": "unit_count",
    "unit.mw_each": "mw_each",
    "unit.hours_permitted": "hours_permitted",
}


def _rest(method: str, path: str, **kw) -> httpx.Response:
    base = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/" + path
    key = os.environ["SUPABASE_SERVICE_KEY"]
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    headers.update(kw.pop("headers", {}))
    r = httpx.request(method, base, headers=headers, timeout=60, **kw)
    r.raise_for_status()
    return r


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _permit_key(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", (s or "").lower())


def resolve_facility(doc_claims: list[dict], permits: list[dict]) -> str | None:
    """Document → facility via permit.no claim match; unique or nothing."""
    hits = set()
    for c in doc_claims:
        if c["field"] != "permit.no":
            continue
        key = _permit_key(c["value"])
        if not key:
            continue
        for p in permits:
            if _permit_key(p["permit_no"]) == key:
                hits.add(p["facility_id"])
    return hits.pop() if len(hits) == 1 else None


def plan_unit_changes(units: list[dict], doc_claims: list[dict]):
    """Pure planner: (links, updates, conflicts).

    links   — [(unit, fact_field, claim)] corroborations, incl. model matches
    updates — {unit_id: {col: (new_value, claim)}} for staged copies
    conflicts — human-readable strings for the review PR
    """
    links, updates, conflicts = [], {}, []
    for u in units:
        token = _norm(u["model"])
        if not token:
            continue
        for c in doc_claims:
            if c["field"] == "unit.model" and (
                    _norm(c["value"]) in token or token in _norm(c["value"])):
                links.append((u, "model", c))
                continue
            col = FIELD_COLS.get(c["field"])
            if col is None or c.get("value_num") is None:
                continue
            if token not in _norm(c.get("quote") or ""):
                continue  # quote doesn't name this unit's model
            new = float(c["value_num"])
            old = u.get(col)
            if old is not None and abs(float(old) - new) < 1e-9:
                links.append((u, col, c))
            else:
                prev = updates.setdefault(u["id"], {}).get(col)
                if prev is not None and abs(prev[0] - new) > 1e-9:
                    conflicts.append(
                        f"unit {u['model']}: {col} proposed both {prev[0]} "
                        f"and {new} — skipped, needs human")
                    updates[u["id"]].pop(col, None)
                else:
                    updates[u["id"]][col] = (new, c)
    return links, updates, conflicts


def ensure_provenance(fact_table: str, fact_id: str, fact_field: str,
                      claim_id: str, note: str) -> bool:
    existing = _rest("GET", "fact_provenance", params={
        "select": "id", "fact_table": f"eq.{fact_table}",
        "fact_id": f"eq.{fact_id}", "fact_field": f"eq.{fact_field}",
        "claim_id": f"eq.{claim_id}"}).json()
    if existing:
        return False
    _rest("POST", "fact_provenance", json=[{
        "fact_table": fact_table, "fact_id": fact_id,
        "fact_field": fact_field, "claim_id": claim_id, "note": note}])
    return True


def staged_copy(unit: dict, changes: dict) -> str:
    """Create or refresh the staging twin of a published unit; returns id."""
    existing = _rest("GET", "unit", params={
        "select": "id", "facility_id": f"eq.{unit['facility_id']}",
        "model": f"eq.{unit['model']}", "fact_state": "eq.staging"}).json()
    patch = {col: (int(new) if col in ("unit_count", "hours_permitted")
                   else new)
             for col, (new, _c) in changes.items()}
    if existing:
        sid = existing[0]["id"]
        _rest("PATCH", "unit", params={"id": f"eq.{sid}"}, json=patch)
        return sid
    row = {k: unit[k] for k in ("facility_id", "oem", "model", "unit_count",
                                "mw_each", "fuel", "hours_permitted")}
    row.update(patch)
    row["fact_state"] = "staging"
    created = _rest("POST", "unit", json=[row],
                    headers={"Prefer": "return=representation"}).json()
    return created[0]["id"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    permits = _rest("GET", "permit", params={
        "select": "id,permit_no,facility_id"}).json()
    facilities = {f["id"]: f for f in _rest("GET", "facility", params={
        "select": "id,slug"}).json()}
    claims = _rest("GET", "claim", params={
        "select": "id,document_id,field,value,value_num,quote,match_score",
        "status": "eq.validated"}).json()

    by_doc: dict[str, list[dict]] = {}
    for c in claims:
        by_doc.setdefault(c["document_id"], []).append(c)

    n_links = n_stage = 0
    for doc_id, doc_claims in sorted(by_doc.items()):
        fac_id = resolve_facility(doc_claims, permits)
        if fac_id is None:
            print(f"UNRESOLVED doc {doc_id[:8]}: no unique permit match "
                  f"({len(doc_claims)} claims held back)")
            continue
        slug = facilities[fac_id]["slug"]
        units = _rest("GET", "unit", params={
            "select": "id,facility_id,oem,model,unit_count,mw_each,fuel,"
                      "hours_permitted",
            "facility_id": f"eq.{fac_id}",
            "fact_state": "eq.published"}).json()
        links, updates, conflicts = plan_unit_changes(units, doc_claims)
        for msg in conflicts:
            print(f"CONFLICT {slug}: {msg}")
        if args.dry_run:
            for u, field, c in links:
                print(f"DRY LINK  {slug}/{u['model']}.{field} <- claim "
                      f"{c['id'][:8]}")
            for uid, chg in updates.items():
                u = next(x for x in units if x["id"] == uid)
                for col, (new, _c) in chg.items():
                    print(f"DRY STAGE {slug}/{u['model']}.{col}: "
                          f"{u.get(col)} -> {new}")
            continue
        for u, field, c in links:
            if ensure_provenance("unit", u["id"], field, c["id"],
                                 f"corroborated by anchored quote "
                                 f"(match {c['match_score']})"):
                n_links += 1
        for uid, chg in updates.items():
            u = next(x for x in units if x["id"] == uid)
            sid = staged_copy(u, chg)
            for col, (new, c) in chg.items():
                ensure_provenance("unit", sid, col, c["id"],
                                  f"staged update {u.get(col)} -> {new}")
                n_stage += 1
                print(f"STAGE {slug}/{u['model']}.{col}: "
                      f"{u.get(col)} -> {new}")
    print(f"normalize: {n_links} new provenance links, "
          f"{n_stage} staged field updates")


if __name__ == "__main__":
    main()
