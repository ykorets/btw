"""btw_engine.bootstrap — new facilities from TCEQ filings and their documents.

normalize.py only updates facilities BTW already tracks. This module creates
the first version of a facility when an agency registry filing (a TCEQ
New Source Review application) has archived documents whose validated,
quote-anchored claims describe its equipment.

Nothing is invented:
- facility identity (name, county, state) is the registry listing, verbatim;
- a document belongs to the filing only through a validated ``permit.no``
  claim equal to the registry permit number;
- a unit cohort exists only where a ``unit.model`` claim exists, and needs a
  ``unit.count`` bound by the model token in its own quote (normalize's
  binding rules); competing values for one field skip that field;
- the permit row is created only when number, authority and status all have
  direct claims, because the schema requires every one of them.

Rows are written as ``staging`` with receipts; ``review --auto`` promotes them
through the same truth gate as every other fact. ``--publish-facilities``
then publishes a staging facility once it has a published unit or permit.

Usage:
  python -m btw_engine.bootstrap            # plan + stage new facilities
  python -m btw_engine.bootstrap --dry-run
  python -m btw_engine.bootstrap --publish-facilities
"""

from __future__ import annotations

import argparse
import re

from btw_engine import normalize
from btw_engine.normalize import (
    _permit_key, _rest, ensure_provenance, plan_unit_changes,
    prepare_unit_receipts, _verification_state)

AUTHORITY = "TCEQ"


def slugify(name: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower())).strip("-")


SMALL_WORDS = {"THE", "AND", "OF", "AT", "ON", "IN"}


def title_case(value: str | None) -> str | None:
    """Registry names are upper case; keep acronyms (NRG, DFW) and codes."""
    if not value:
        return value

    def word(w: str) -> str:
        if any(c.isdigit() for c in w):
            return w
        if len(w) <= 3 and w.isupper() and w not in SMALL_WORDS:
            return w
        return w.capitalize()
    return " ".join(word(w) for w in value.split())


def _token(value: str | None) -> str:
    return normalize._norm(value or "")


def _binding_text(claim: dict) -> str:
    return _token((claim.get("quote") or "") + " "
                  + (claim.get("entity_hint") or ""))


def model_cohorts(claims: list[dict]) -> list[tuple[str, dict]]:
    """Distinct models, longest wording kept ("7HA" folds into "7HA.01")."""
    models = [c for c in claims if c.get("field") == "unit.model"
              and _token(c.get("value"))]
    models.sort(key=lambda c: len(_token(c["value"])), reverse=True)
    kept: list[tuple[str, dict]] = []
    for claim in models:
        token = _token(claim["value"])
        if any(token in other or other in token for other, _c in kept):
            continue
        kept.append((token, claim))
    return kept


def _unique_text_claim(claims: list[dict], field: str, token: str
                       ) -> dict | None:
    hits = [c for c in claims if c.get("field") == field
            and token in _binding_text(c) and _token(c.get("value"))]
    values = {_token(c["value"]) for c in hits}
    return hits[0] if len(values) == 1 else None


def plan_units(claims: list[dict]) -> tuple[list[dict], list[str]]:
    """Validated claims of one filing -> new unit cohorts with receipts."""
    cohorts = model_cohorts(claims)
    skeletons = [{"id": f"new-{i}", "model": claim["value"]}
                 for i, (_token_, claim) in enumerate(cohorts)]
    _links, updates, conflicts = plan_unit_changes(skeletons, claims)
    units, notes = [], list(conflicts)
    for skeleton, (token, model_claim) in zip(skeletons, cohorts):
        changes = updates.get(skeleton["id"], {})
        if "unit_count" not in changes:
            notes.append(f"{skeleton['model']}: no unambiguous unit count")
            continue
        links = [("model", model_claim)]
        row = {"model": model_claim["value"], "oem": None, "fuel": None}
        oem = _unique_text_claim(claims, "unit.oem", token)
        if oem is not None:
            row["oem"] = oem["value"]
            links.append(("oem", oem))
        fuel = _unique_text_claim(claims, "unit.fuel", token)
        if fuel is not None:
            row["fuel"] = fuel["value"]
            links.append(("fuel", fuel))
        plan, _skipped, error = prepare_unit_receipts(links, changes)
        if error:
            notes.append(f"{skeleton['model']}: {error}")
            continue
        kept = {field for field, _claim in plan["unit_links"]}
        for field in ("oem", "fuel"):
            if field not in kept:
                row[field] = None
        for col, (value, _claim, _method) in changes.items():
            row[col] = int(value) if col in ("unit_count", "hours_permitted") else value
        units.append({"row": row, "plan": plan, "changes": changes})
    return units, notes


def plan_permit(record_no: str, claims: list[dict]) -> dict | None:
    key = _permit_key(record_no)
    number = next((c for c in claims if c.get("field") == "permit.no"
                   and _permit_key(c.get("value")) == key), None)
    authority = next((c for c in claims if c.get("field") == "permit.authority"
                      and re.search(r"\bTCEQ\b", f"{c.get('value')} {c.get('quote')}")),
                     None)
    statuses = [c for c in claims if c.get("field") == "permit.status"
                and _token(c.get("value"))]
    if not (number and authority and statuses):
        return None
    if len({_token(c["value"]) for c in statuses}) != 1:
        return None
    status = statuses[0]
    verification, derivation = _verification_state([number, authority, status])
    return {
        "row": {"authority": AUTHORITY, "permit_no": record_no,
                "status": status["value"].strip(),
                "verification_state": verification},
        "receipts": [("authority", authority), ("permit_no", number),
                     ("status", status)],
        "derivation": derivation,
    }


def plan_facility(filing: dict, claims: list[dict]) -> dict | None:
    units, notes = plan_units(claims)
    if not units:
        return {"skip": True, "notes": notes or ["no unit cohorts"]}
    permit = plan_permit(filing["record_no"], claims)
    status = _token(permit["row"]["status"]) if permit else ""
    issued = "issued" in status or "authorized" in status
    return {
        "skip": False,
        "notes": notes,
        "facility": {
            "slug": slugify(f"{filing['name']} {filing.get('county') or ''} tx"),
            "name": title_case(filing["name"]),
            "state": "TX",
            "county": title_case(filing.get("county")),
            "status": "permitted" if issued else "filed",
        },
        "units": units,
        "permit": permit,
    }


def scoped_claims(record_no: str, claims_by_doc: dict[str, list[dict]]
                  ) -> list[dict]:
    """All validated claims of documents that name this permit number."""
    key = _permit_key(record_no)
    out = []
    for doc_claims in claims_by_doc.values():
        if any(c.get("field") == "permit.no"
               and _permit_key(c.get("value")) == key for c in doc_claims):
            out.extend(doc_claims)
    return out


# ---------------------------------------------------------------- writes --

def _post(table: str, row: dict) -> dict:
    return _rest("POST", table, json=[row],
                 headers={"Prefer": "return=representation"}).json()[0]


def unit_receipts(unit: dict) -> list[tuple[str, dict, str, str, str | None]]:
    """(fact_field, claim, note, support_kind, derivation) for one cohort."""
    p = unit["plan"]
    out = [(field, claim, "new facility: anchored registry document claim",
            "direct", None) for field, claim in p["unit_links"]]
    out += [(col, claim, f"new facility: bound by {method}", "direct", None)
            for col, (_value, claim, method) in unit["changes"].items()
            if claim is not None]
    out.append(("basis", p["representative"], "engine classification",
                "derived", p["basis_derivation"]))
    out.append(("verification_state", p["representative"],
                "engine classification", "derived",
                p["verification_derivation"]))
    return out


def permit_receipts(permit: dict) -> list[tuple[str, dict, str, str, str | None]]:
    out = [(field, claim, "new facility: anchored registry document claim",
            "direct", None) for field, claim in permit["receipts"]]
    out.append(("verification_state", permit["receipts"][0][1],
                "engine classification", "derived", permit["derivation"]))
    return out


def unit_row(unit: dict) -> dict:
    p = unit["plan"]
    return {**unit["row"], "basis": p["basis"],
            "verification_state": p["verification"]}


def stage_facility(plan: dict) -> str:
    fac = plan["facility"]
    existing = _rest("GET", "facility", params={
        "select": "id", "slug": f"eq.{fac['slug']}"}).json()
    facility_id = (existing[0]["id"] if existing
                   else _post("facility", {**fac, "fact_state": "staging"})["id"])
    for unit in plan["units"]:
        uid = _post("unit", {**unit_row(unit), "facility_id": facility_id,
                             "fact_state": "staging"})["id"]
        for field, claim, note, support, derivation in unit_receipts(unit):
            ensure_provenance("unit", uid, field, claim["id"], note,
                              support, derivation)
    permit = plan["permit"]
    if permit:
        pid = _post("permit", {**permit["row"], "facility_id": facility_id,
                               "fact_state": "staging"})["id"]
        for field, claim, note, support, derivation in permit_receipts(permit):
            ensure_provenance("permit", pid, field, claim["id"], note,
                              support, derivation)
    return facility_id


def tracked_permit_keys() -> set[str]:
    rows = _rest("GET", "permit", params={"select": "permit_no"}).json()
    return {_permit_key(r["permit_no"]) for r in rows}


def tracked_facility_slugs() -> set[str]:
    rows = _rest("GET", "facility", params={"select": "slug"}).json()
    return {r["slug"] for r in rows}


# Behind-the-meter scope: a data center / compute site, or a new power plant
# application. Renewals of decades-old utility plants are not new facilities.
COMPUTE_NAME = re.compile(
    r"DATA ?CENTER|DATACENTER|TECHNOLOGY CENTER|TECH CENTER|COMPUT|"
    r"HYPERSCALE|CLOUD|DIGITAL|CAMPUS|\bAI\b|FERMI", re.I)


def in_scope(filing: dict) -> bool:
    return bool(COMPUTE_NAME.search(filing.get("name") or "")
                or (filing.get("registry_status") or "").upper() == "NEW APPLICATION")


def tceq_filings(filings: list[dict]) -> list[dict]:
    """In-scope TCEQ registry rows with a permit number; NSR numbers before
    PSD/GHG numbers of the same project, oldest sighting first in each group."""
    rows = [f for f in filings
            if f["source_id"] == "tceq-permit-portal" and f.get("record_no")
            and in_scope(f)]
    return sorted(rows, key=lambda f: (not str(f["record_no"])[:1].isdigit(),
                                       str(f.get("first_seen") or "")),
                  )


def run(dry_run: bool = False) -> int:
    from btw_engine.publish import build_filings, fetch_filing_candidates

    filings = tceq_filings(build_filings(fetch_filing_candidates()))
    claims = _rest("GET", "claim", params={
        "select": "id,document_id,entity_hint,field,value,value_num,quote,"
                  "page,match_score,numeric_check,anchor,extractor_version,"
                  "document:document_id(doc_genre)",
        "status": "eq.validated"}).json()
    by_doc: dict[str, list[dict]] = {}
    for claim in claims:
        by_doc.setdefault(claim["document_id"], []).append(claim)

    tracked = tracked_permit_keys()
    slugs = tracked_facility_slugs()
    # PSDTX/GHGPSDTX numbers of one project share its documents; plan each
    # project once, under the first registry row that names it.
    claimed_docs: set[str] = set()
    staged = 0
    for filing in filings:
        if _permit_key(filing["record_no"]) in tracked:
            continue
        scope = scoped_claims(filing["record_no"], by_doc)
        docs = {c["document_id"] for c in scope}
        if not scope or docs & claimed_docs:
            continue
        plan = plan_facility(filing, scope)
        label = f"{filing['name']} ({filing['record_no']})"
        if plan["skip"]:
            print(f"WAIT {label}: {'; '.join(plan['notes'])}")
            continue
        if plan["facility"]["slug"] in slugs:
            print(f"SKIP {label}: facility {plan['facility']['slug']} exists")
            claimed_docs |= docs
            continue
        units = ", ".join(
            f"{u['row']['unit_count']}x {u['row']['oem'] or ''} {u['row']['model']}"
            f"{' @ ' + format(u['row'].get('mw_each'), 'g') + ' MW' if u['row'].get('mw_each') else ''}"
            for u in plan["units"])
        print(f"{'DRY ' if dry_run else ''}NEW {label}: {units}; permit "
              f"{'yes' if plan['permit'] else 'no (missing status/authority claim)'}")
        for note in plan["notes"]:
            print(f"  note: {note}")
        claimed_docs |= docs
        if not dry_run:
            stage_facility(plan)
            slugs.add(plan["facility"]["slug"])
            staged += 1
    print(f"bootstrap: {staged} new facilities staged")
    return staged


def publish_facilities() -> int:
    """Publish staging facilities that now have a published unit or permit."""
    staging = _rest("GET", "facility", params={
        "select": "id,slug,unit(id,fact_state),permit(id,fact_state)",
        "fact_state": "eq.staging"}).json()
    published = 0
    for fac in staging:
        rows = (fac.get("unit") or []) + (fac.get("permit") or [])
        if any(r.get("fact_state") == "published" for r in rows):
            _rest("PATCH", "facility", params={"id": f"eq.{fac['id']}"},
                  json={"fact_state": "published"})
            print(f"published facility {fac['slug']}")
            published += 1
    return published


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--publish-facilities", action="store_true")
    args = ap.parse_args()
    if args.publish_facilities:
        publish_facilities()
    else:
        run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
