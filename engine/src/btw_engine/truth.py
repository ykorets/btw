"""Fail-closed provenance checks shared by publish and review.

Having a ``fact_provenance`` row is not enough.  The linked claim must be
validated, archived, anchored, field-compatible, and support the value that
would be shown. Derived classifications require an explicit derivation.
"""

from __future__ import annotations

import math
import re
from datetime import datetime


FACT_FIELDS = {
    "unit": ("oem", "model", "unit_count", "mw_each", "total_mw", "fuel",
             "hours_permitted", "basis", "verification_state"),
    "permit": ("authority", "permit_no", "permit_type", "status",
               "filed_at", "issued_at", "verification_state"),
}

CLAIM_FIELD = {
    ("unit", "oem"): "unit.oem",
    ("unit", "model"): "unit.model",
    ("unit", "mw_each"): "unit.mw_each",
    ("unit", "total_mw"): "unit.mw_total",
    ("unit", "fuel"): "unit.fuel",
    ("unit", "hours_permitted"): "unit.hours_permitted",
    ("permit", "authority"): "permit.authority",
    ("permit", "permit_no"): "permit.no",
    ("permit", "permit_type"): "permit.type",
    ("permit", "status"): "permit.status",
    ("permit", "filed_at"): "permit.filed_at",
    ("permit", "issued_at"): "permit.issued_at",
}

NUMERIC_FIELDS = {
    ("unit", "unit_count"),
    ("unit", "mw_each"),
    ("unit", "total_mw"),
    ("unit", "hours_permitted"),
}


def _norm(value) -> str:
    return re.sub(r"[^0-9a-z]+", "", str(value or "").lower())


def _date(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _text_supports(value, claim: dict) -> bool:
    want = _norm(value)
    got = _norm(claim.get("value"))
    if not want or not got:
        return False
    if want in got or got in want:
        return True
    # Canonical acronyms such as TCEQ/MDEQ must occur explicitly in the
    # extracted value or quote; the source document's domain is not evidence.
    raw = str(value or "")
    if raw.isupper() and 2 <= len(raw) <= 8:
        haystack = f"{claim.get('value') or ''} {claim.get('quote') or ''}"
        return bool(re.search(rf"\b{re.escape(raw)}\b", haystack,
                              flags=re.IGNORECASE))
    return False


def _anchor_is_valid(claim: dict) -> bool:
    if claim.get("status") != "validated":
        return False
    document = claim.get("document") or {}
    if not document.get("r2_key"):
        return False
    anchor = claim.get("anchor")
    if anchor == "quote":
        return bool(claim.get("quote") and
                    float(claim.get("match_score") or 0) >= 0.92)
    if anchor in {"cell", "figure"}:
        return claim.get("page") is not None and claim.get("bbox") is not None
    return False


def _expected_claim_fields(table: str, field: str, fact: dict) -> set[str]:
    if table == "unit" and field == "unit_count":
        return ({"observation.unit_count"} if fact.get("basis") == "observed"
                else {"unit.count"})
    if table == "unit" and field == "total_mw":
        return {"unit.mw_total", "observation.mw"}
    expected = CLAIM_FIELD.get((table, field))
    return {expected} if expected else set()


def receipt_supports(table: str, field: str, value, fact: dict,
                     receipt: dict) -> bool:
    """Mirror the database semantic gate for one provenance receipt."""
    claim = receipt.get("claim") or {}
    if not _anchor_is_valid(claim):
        return False
    support_kind = receipt.get("support_kind") or "direct"
    if support_kind == "derived":
        if not str(receipt.get("derivation") or "").strip():
            return False
        if field == "verification_state":
            claim_field = claim.get("field", "")
            return (claim_field.startswith(("unit.", "observation."))
                    if table == "unit"
                    else claim_field.startswith("permit."))
        if table == "unit" and field == "basis":
            claim_field = claim.get("field", "")
            basis = fact.get("basis")
            return ((basis == "permitted" and claim_field.startswith("unit."))
                    or (basis == "observed"
                        and claim_field.startswith("observation."))
                    or (basis == "reported" and claim_field in {
                        "unit.count", "unit.mw_total",
                        "observation.unit_count", "observation.mw",
                    }))
        return False
    if support_kind != "direct":
        return False
    if claim.get("field") not in _expected_claim_fields(table, field, fact):
        return False
    if (table, field) in NUMERIC_FIELDS:
        number = claim.get("value_num")
        return bool(claim.get("numeric_check") is True and
                    number is not None and
                    math.isclose(float(value), float(number),
                                 rel_tol=0, abs_tol=1e-9))
    if table == "permit" and field in {"filed_at", "issued_at"}:
        return _date(value) == _date(claim.get("value"))
    return _text_supports(value, claim)


def provenance_violations(facilities: list[dict],
                          provenance: list[dict]) -> list[str]:
    """Return one deterministic message for every unsafe published field."""
    indexed: dict[tuple[str, str, str], list[dict]] = {}
    for row in provenance:
        key = (row.get("fact_table"), str(row.get("fact_id")),
               row.get("fact_field"))
        indexed.setdefault(key, []).append(row)

    violations = []
    for facility in facilities:
        slug = facility.get("slug") or facility.get("name") or "unknown"
        for table, collection in (("unit", facility.get("unit", [])),
                                  ("permit", facility.get("permit", []))):
            for fact in collection:
                fact_id = str(fact.get("id"))
                identity = (fact.get("model") or fact.get("permit_no")
                            or fact_id)
                label = f"{slug}/{identity}"
                required = (("basis", "verification_state")
                            if table == "unit"
                            else ("verification_state",))
                for field in required:
                    if field in fact and fact.get(field) in (None, ""):
                        violations.append(
                            f"{table} {label}.{field}: required review "
                            "classification is missing")
                for field in FACT_FIELDS[table]:
                    value = fact.get(field)
                    if value is None or value == "":
                        continue
                    rows = indexed.get((table, fact_id, field), [])
                    if table == "permit" and field == "status":
                        components = [part.strip() for part in
                                      re.split(r"[;,]", str(value))
                                      if part.strip()]
                        missing = [part for part in components
                                   if not any(receipt_supports(
                                       table, field, part, fact, row)
                                       for row in rows)]
                        if missing:
                            violations.append(
                                f"{table} {label}.{field}: unsupported "
                                f"component(s): {', '.join(missing)}")
                        continue
                    if not any(receipt_supports(table, field, value, fact, row)
                               for row in rows):
                        expected = "/".join(sorted(
                            _expected_claim_fields(table, field, fact)))
                        support = "direct"
                        if field in {"basis", "verification_state"}:
                            expected = "compatible derived"
                            support = "derived"
                        violations.append(
                            f"{table} {label}.{field}: no {support} validated "
                            f"archived {expected} claim for {value!r}")
    return violations


def assert_provenance(facilities: list[dict],
                      provenance: list[dict], *, gate: str) -> None:
    violations = provenance_violations(facilities, provenance)
    if violations:
        detail = "\n".join(f"- {item}" for item in violations)
        raise SystemExit(
            f"{gate}: {len(violations)} provenance violation(s); "
            f"refusing to continue.\n{detail}")
