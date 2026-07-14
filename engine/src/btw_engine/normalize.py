"""btw_engine.normalize — validated claims → provenance + staged updates (M5,
sentence-context binding added in M3.5).

Deterministic only; nothing is invented:
- A document resolves to a facility iff one of its validated permit.no claims
  matches a known permit (non-alphanumerics stripped on both sides).
- A numeric unit claim (count / mw_each / hours_permitted) binds to a unit iff
  the unit's model token appears verbatim in the claim's own quote, OR —
  when the quote names no model — exactly one unit's model token appears in
  the sentence context around the quote in the archived page text (fetched
  from R2, quote located by fuzzy alignment). Ambiguous context (two models
  or no match) never binds.
- Bound claim, value == published  → evidence-only staging version with a
  new fact_provenance receipt. Published versions are never patched in place.
- Bound claim, value != published  → staged update: a fact_state='staging'
  copy of the unit carrying the new value, with provenance rows per changed
  field. The review PR seals exact ids; review.py --promote swaps them in one
  database transaction.
- Unresolved documents and unbound claims are reported, never guessed.

Usage: python -m btw_engine.normalize [--dry-run]
Env: SUPABASE_URL, SUPABASE_SERVICE_KEY; R2_* optional — without R2 access
context binding is skipped and quote-token binding still works.
"""

import argparse
import os
import re
from datetime import datetime

import httpx
from rapidfuzz import fuzz

CONTEXT_RADIUS = 240          # chars around the located quote
CONTEXT_LOCATE_THRESHOLD = 85  # partial_ratio_alignment score to trust locate

FIELD_COLS = {
    "unit.count": "unit_count",
    "unit.mw_each": "mw_each",
    "unit.mw_total": "total_mw",
    "observation.unit_count": "unit_count",
    "observation.mw": "total_mw",
    "unit.hours_permitted": "hours_permitted",
}

TEXT_FIELD_COLS = {
    "unit.oem": "oem",
    "unit.fuel": "fuel",
}

PERMIT_FIELD_COLS = {
    "permit.no": "permit_no",
    "permit.authority": "authority",
    "permit.type": "permit_type",
}


def _rest(method: str, path: str, **kw) -> httpx.Response:
    base = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/" + path
    key = os.environ["SUPABASE_SERVICE_KEY"]
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    headers.update(kw.pop("headers", {}))
    r = httpx.request(method, base, headers=headers, timeout=60, **kw)
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # PostgREST returns the database constraint/trigger explanation in
        # the response body. Preserve it in CI without ever printing auth.
        detail = (r.text or "").strip().replace("\n", " ")[:1000]
        raise RuntimeError(
            f"Supabase {method} {path} failed ({r.status_code}): {detail}"
        ) from exc
    return r


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _permit_key(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", (s or "").lower())


def resolve_facility(doc_claims: list[dict], permits: list[dict],
                     provenance_facilities: set[str] | None = None
                     ) -> str | None:
    """Document → facility via permit number, then existing provenance.

    A court filing often names the plant but not the regulator's exact permit
    number.  Reusing an already reviewed receipt from the same archived
    document is deterministic and avoids guessing from names or geography.
    The fallback is accepted only when every existing unit/permit receipt for
    the document points to one facility.
    """
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
    if len(hits) == 1:
        return hits.pop()
    fallback = set(provenance_facilities or set())
    return fallback.pop() if not hits and len(fallback) == 1 else None


def resolve_permit(doc_claims: list[dict], permits: list[dict]) -> dict | None:
    """Document -> one permit via an exact normalized permit number."""
    hits: dict[str, dict] = {}
    for c in doc_claims:
        if c["field"] != "permit.no":
            continue
        key = _permit_key(c.get("value") or "")
        if not key:
            continue
        for p in permits:
            if _permit_key(p["permit_no"]) == key:
                hits[p["id"]] = p
    return next(iter(hits.values())) if len(hits) == 1 else None


def _date_value(value: str | None) -> str | None:
    """Normalize the date formats produced by the extractor to ISO."""
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _text_supports(published: str | None, claim: dict) -> bool:
    """True only for direct text containment or an explicit acronym."""
    want = _norm(published or "")
    got = _norm(claim.get("value") or "")
    if not want or not got:
        return False
    if want in got or got in want:
        return True
    quote = _norm(claim.get("quote") or "")
    return bool(len(want) >= 3 and " " not in want
                and re.search(rf"\b{re.escape(want)}\b", quote))


def plan_permit_changes(permit: dict, doc_claims: list[dict]):
    """Plan direct permit receipts and unambiguous staged corrections.

    Exact claims corroborate the current value. A unique typed value may fill
    a missing field, but never silently replace an existing categorical or
    date value: a later amendment, a location mistaken for an authority, or
    a workflow label mistaken for status must remain an explicit conflict.
    """
    links, changes, conflicts = [], {}, []
    candidates: dict[str, list[tuple[object, dict, str]]] = {}

    def add(col, value, claim, method):
        candidates.setdefault(col, []).append((value, claim, method))

    for c in doc_claims:
        field = c.get("field")
        if field in {"permit.filed_at", "permit.issued_at"}:
            iso = _date_value(c.get("value"))
            col = field.removeprefix("permit.")
            if iso:
                add(col, iso, c, "exact typed date")
            continue
        if field == "permit.date":
            # Legacy extraction output is intentionally held.  A bare date
            # cannot distinguish application receipt from permit issuance.
            continue
        if field == "permit.status":
            got = _norm(c.get("value") or "")
            if got:
                add("status", c.get("value").strip(), c,
                    "status component")
            continue
        col = PERMIT_FIELD_COLS.get(field)
        if col == "permit_no":
            if (_permit_key(c.get("value") or "")
                    == _permit_key(permit["permit_no"])):
                links.append((permit, col, c, "exact permit number"))
        elif col:
            add(col, c.get("value"), c, "text match")

    for col, rows in candidates.items():
        current = permit.get(col)
        if col == "status":
            parts = [_norm(x) for x in re.split(r"[;,]", current or "")]
            exact = [row for row in rows if _norm(row[0]) in parts]
            for _value, claim, method in exact:
                links.append((permit, col, claim, method))
            supported = {_norm(value) for value, _claim, _method in exact}
            missing = [part for part in parts if part not in supported]
            if exact and not missing:
                continue
            unique: dict[str, tuple[object, dict, str]] = {}
            for row in rows:
                unique.setdefault(_norm(row[0]), row)
            if current not in (None, ""):
                candidates = ", ".join(
                    str(row[0]) for row in unique.values()) or "none"
                conflicts.append(
                    f"permit {permit['permit_no']}: status lacks exact typed "
                    f"support for {', '.join(missing) or current}; candidates "
                    f"{candidates}; existing value was not overwritten")
            elif len(unique) == 1:
                value, claim, _method = next(iter(unique.values()))
                changes[col] = (value, [claim], "unique typed status")
            elif len(unique) > 1:
                conflicts.append(
                    f"permit {permit['permit_no']}: status has "
                    f"{len(unique)} competing typed values")
            continue

        exact = [row for row in rows if (
            str(current) == str(row[0]) if col in {"filed_at", "issued_at"}
            else _text_supports(current, row[1]))]
        if exact:
            for _value, claim, method in exact:
                links.append((permit, col, claim, method))
            continue
        unique: dict[str, tuple[object, dict, str]] = {}
        for row in rows:
            key = str(row[0]) if col in {"filed_at", "issued_at"} else _norm(row[0])
            unique.setdefault(key, row)
        if current not in (None, "") and unique:
            candidates = ", ".join(str(row[0]) for row in unique.values())
            conflicts.append(
                f"permit {permit['permit_no']}: {col} current value "
                f"{current!r} has no exact typed support; candidates "
                f"{candidates}; existing value was not overwritten")
        elif len(unique) == 1:
            value, claim, method = next(iter(unique.values()))
            changes[col] = (value, [claim], method)
        elif len(unique) > 1:
            conflicts.append(
                f"permit {permit['permit_no']}: {col} has "
                f"{len(unique)} competing typed values")
    return links, changes, conflicts


def plan_permit_links(permit: dict, doc_claims: list[dict]):
    """Compatibility wrapper for callers interested only in receipts."""
    return plan_permit_changes(permit, doc_claims)[0]


def locate_context(page_text: str, quote: str,
                   radius: int = CONTEXT_RADIUS) -> str | None:
    """Find the quote in the page (fuzzy, whitespace-normalized) and return
    the surrounding window, or None if the quote can't be located."""
    nt, nq = _norm(page_text), _norm(quote)
    if not nt or not nq:
        return None
    al = fuzz.partial_ratio_alignment(nq, nt)
    if al is None or al.score < CONTEXT_LOCATE_THRESHOLD:
        return None
    return nt[max(0, al.dest_start - radius): al.dest_end + radius]


def _sole_context_unit(units: list[dict], context: str) -> dict | None:
    """The unit bound by sentence context — iff exactly one unit's model
    token appears in the window. Two models nearby = ambiguous = no bind."""
    hits = [u for u in units if _norm(u["model"])
            and _norm(u["model"]) in context]
    return hits[0] if len(hits) == 1 else None


def plan_unit_changes(units: list[dict], doc_claims: list[dict],
                      context_of=None):
    """Pure planner: (links, updates, conflicts).

    links   — [(unit, fact_field, claim)] corroborations, incl. model matches
    updates — {unit_id: {col: (new_value, claim, method)}} for staged copies;
              method is 'quote' or 'context'
    conflicts — human-readable strings for the review PR
    context_of — optional callable claim -> str|None returning the sentence
              window around the claim's quote in the archived page text;
              used only when the quote itself names no model token (M3.5).
    """
    links, updates, conflicts = [], {}, []
    numeric_candidates: dict[tuple[str, str], list[tuple[dict, dict, str]]] = {}
    tokens = [_norm(u["model"]) for u in units]

    def bind(u, c, col, method):
        numeric_candidates.setdefault((u["id"], col), []).append(
            (u, c, method))

    for u in units:
        token = _norm(u["model"])
        if not token:
            continue
        for c in doc_claims:
            if c["field"] == "unit.model" and (
                    _norm(c["value"]) in token or token in _norm(c["value"])):
                links.append((u, "model", c))
                continue
            text_col = TEXT_FIELD_COLS.get(c["field"])
            if text_col is not None:
                binding_text = _norm((c.get("quote") or "") + " "
                                     + (c.get("entity_hint") or ""))
                if (token in binding_text
                        and _text_supports(u.get(text_col), c)):
                    links.append((u, text_col, c))
                continue
            col = FIELD_COLS.get(c["field"])
            if col is None or c.get("value_num") is None:
                continue
            binding_text = _norm((c.get("quote") or "") + " "
                                 + (c.get("entity_hint") or ""))
            if token not in binding_text:
                continue  # quote doesn't name this unit's model
            bind(u, c, col, "quote")

    # Second pass: claims whose quote names NO model token at all — try the
    # sentence context around the quote in the source page (M3.5).
    if context_of is not None:
        for c in doc_claims:
            col = FIELD_COLS.get(c["field"])
            if col is None or c.get("value_num") is None:
                continue
            nq = _norm((c.get("quote") or "") + " "
                       + (c.get("entity_hint") or ""))
            if any(t and t in nq for t in tokens):
                continue  # already handled by quote-token binding
            ctx = context_of(c)
            if not ctx:
                continue
            u = _sole_context_unit(units, ctx)
            if u is not None:
                bind(u, c, col, "context")

    # A facility-scoped observation or cohort total can safely bind to the
    # sole unit row.  Equipment-specific permit rows still require a model
    # token, so a mixed permit envelope cannot collapse into one cohort.
    if len(units) == 1:
        u = units[0]
        for c in doc_claims:
            col = FIELD_COLS.get(c.get("field"))
            if (col is None or c.get("value_num") is None
                    or c.get("field") not in {
                        "observation.unit_count", "observation.mw",
                        "unit.mw_total"}):
                continue
            key = (u["id"], col)
            if not any(existing is c for _unit, existing, _method
                       in numeric_candidates.get(key, [])):
                bind(u, c, col, "sole facility cohort")

    # Decide only after seeing every candidate. Historical observations often
    # contain several valid counts. If one corroborates the published value,
    # retain it and do not let a later observation silently overwrite it.
    for (uid, col), rows in numeric_candidates.items():
        u = rows[0][0]
        old = u.get(col)
        exact = [row for row in rows if old is not None and
                 abs(float(old) - float(row[1]["value_num"])) < 1e-9]
        if exact:
            links.extend((u, col, claim) for _u, claim, _method in exact)
            other = {float(claim["value_num"]) for _u, claim, _method in rows
                     if abs(float(old) - float(claim["value_num"])) >= 1e-9}
            if other:
                conflicts.append(
                    f"unit {u.get('model') or uid}: {col} also has "
                    f"historical/competing values {sorted(other)}; retained "
                    f"corroborated published value {old}")
            continue
        by_value: dict[float, tuple[dict, dict, str]] = {}
        for row in rows:
            by_value.setdefault(float(row[1]["value_num"]), row)
        if len(by_value) == 1:
            new, (_u, claim, method) = next(iter(by_value.items()))
            updates.setdefault(uid, {})[col] = (new, claim, method)
        elif len(by_value) > 1:
            conflicts.append(
                f"unit {u.get('model') or uid}: {col} proposed "
                f"{sorted(by_value)} — skipped, needs human")
    return links, updates, conflicts


def ensure_provenance(fact_table: str, fact_id: str, fact_field: str,
                      claim_id: str, note: str,
                      support_kind: str = "direct",
                      derivation: str | None = None) -> bool:
    existing = _rest("GET", "fact_provenance", params={
        "select": "id", "fact_table": f"eq.{fact_table}",
        "fact_id": f"eq.{fact_id}", "fact_field": f"eq.{fact_field}",
        "claim_id": f"eq.{claim_id}"}).json()
    if existing:
        return False
    _rest("POST", "fact_provenance", json=[{
        "fact_table": fact_table, "fact_id": fact_id,
        "fact_field": fact_field, "claim_id": claim_id, "note": note,
        "support_kind": support_kind, "derivation": derivation}])
    return True


def staged_copy(unit: dict, changes: dict, *, basis: str,
                verification_state: str) -> str:
    """Create or refresh the staging twin of a published unit; returns id."""
    existing = _rest("GET", "unit", params={
        "select": "id", "logical_id": f"eq.{unit['logical_id']}",
        "fact_state": "eq.staging"}).json()
    patch = {col: (int(new) if new is not None
                   and col in ("unit_count", "hours_permitted") else new)
             for col, (new, *_rest_) in changes.items()}
    patch.update({"basis": basis, "verification_state": verification_state})
    if existing:
        sid = existing[0]["id"]
        _rest("PATCH", "unit", params={"id": f"eq.{sid}"}, json=patch)
        return sid
    row = {k: unit.get(k) for k in (
        "logical_id", "facility_id", "oem", "model", "unit_count",
        "mw_each", "total_mw", "fuel", "hours_permitted")}
    row.update(patch)
    row["fact_state"] = "staging"
    created = _rest("POST", "unit", json=[row],
                    headers={"Prefer": "return=representation"}).json()
    return created[0]["id"]


def staged_permit_copy(permit: dict, changes: dict, *,
                       verification_state: str) -> str:
    """Create the staging version that receives new permit evidence."""
    existing = _rest("GET", "permit", params={
        "select": "id", "logical_id": f"eq.{permit['logical_id']}",
        "fact_state": "eq.staging"}).json()
    patch = {col: value for col, (value, *_rest_) in changes.items()}
    patch["verification_state"] = verification_state
    if existing:
        sid = existing[0]["id"]
        _rest("PATCH", "permit", params={"id": f"eq.{sid}"}, json=patch)
        return sid
    row = {key: permit.get(key) for key in (
        "logical_id", "facility_id", "authority", "permit_no", "permit_type",
        "status", "filed_at", "issued_at")}
    row.update(patch)
    row["fact_state"] = "staging"
    created = _rest("POST", "permit", json=[row],
                    headers={"Prefer": "return=representation"}).json()
    return created[0]["id"]


def copy_provenance(fact_table: str, source_id: str, target_id: str,
                    exclude_fields: set[str] | None = None) -> int:
    """Copy unchanged receipts to a staged version before review.

    Promotion changes row identity.  Without this copy, a staged row carries
    only the changed field's receipt and silently loses provenance for every
    unchanged field when it becomes published.
    """
    rows = _rest("GET", "fact_provenance", params={
        "select": "fact_field,claim_id,note,support_kind,derivation",
        "fact_table": f"eq.{fact_table}",
        "fact_id": f"eq.{source_id}",
    }).json()
    copied = 0
    for row in rows:
        if row["fact_field"] in (exclude_fields or set()):
            continue
        if ensure_provenance(fact_table, target_id, row["fact_field"],
                             row["claim_id"],
                             f"inherited from prior fact version: "
                             f"{row.get('note') or ''}".strip(),
                             row.get("support_kind") or "direct",
                             row.get("derivation")):
            copied += 1
    return copied


def _unit_basis(claims: list[dict]) -> tuple[str, str]:
    """Classify a unit cohort from the claims that support its facts.

    Equipment metadata (for example ``unit.model``) and unrelated imagery
    must not override the evidence class of count/capacity facts. A permit
    cohort can legitimately have satellite evidence in the same facility
    dossier; that does not make a permit-authorized count an observation.
    """
    fields = {claim.get("field") or "" for claim in claims}
    # Count defines the cohort more strongly than a capacity assertion. A
    # reported total may coexist with an independently observed count without
    # turning that observed cohort into a reported/permitted one.
    if ("observation.unit_count" in fields
            and "unit.count" not in fields):
        return "observed", "observation.* claim classifies the cohort as observed"
    unit_quantitative = {
        "unit.count", "unit.mw_each", "unit.mw_total",
        "unit.hours_permitted",
    }
    if ("observation.mw" in fields and not fields & unit_quantitative):
        return "observed", "observation.* claim classifies the cohort as observed"
    genres = {
        ((claim.get("document") or {}).get("doc_genre") or "").lower()
        for claim in claims
    }
    if any(any(token in genre for token in
               ("permit", "tceq", "mdeq", "air", "psd", "regulatory"))
           for genre in genres):
        return "permitted", "regulatory document classifies the cohort as permitted"
    return "reported", "non-regulatory source classifies the cohort as reported"


def _unit_receipt_compatible(basis: str, fact_field: str,
                             claim: dict) -> bool:
    """Local mirror of migration 008's direct field compatibility gate.

    This preflight prevents a deterministic semantic error from leaving a
    half-populated staging version. Database triggers remain authoritative.
    """
    claim_field = claim.get("field") or ""
    expected = {
        "oem": {"unit.oem"},
        "model": {"unit.model"},
        "mw_each": {"unit.mw_each"},
        "total_mw": {"unit.mw_total", "observation.mw"},
        "fuel": {"unit.fuel"},
        "hours_permitted": {"unit.hours_permitted"},
    }
    if fact_field == "unit_count":
        allowed = ({"observation.unit_count"} if basis == "observed"
                   else {"unit.count"})
    else:
        allowed = expected.get(fact_field, set())
    return claim_field in allowed


def _basis_claim(claims: list[dict], basis: str) -> dict:
    """Choose a receipt that migration 008 accepts for derived ``basis``."""
    if basis == "reported":
        # Keep this exactly aligned with migration 008. A manufacturer/model
        # claim identifies equipment but cannot establish a reported cohort.
        for field in ("unit.count", "unit.mw_total",
                      "observation.unit_count", "observation.mw"):
            for claim in claims:
                if (claim.get("field") or "") == field:
                    return claim
        raise ValueError("no compatible quantitative claim derives reported basis")
    prefix = "observation." if basis == "observed" else "unit."
    for claim in claims:
        if (claim.get("field") or "").startswith(prefix):
            return claim
    raise ValueError(f"no compatible claim can derive unit basis {basis!r}")


def prepare_unit_receipts(unit_links: list[tuple[str, dict]],
                          changes: dict) -> tuple[dict | None,
                                                   list[tuple[str, dict]],
                                                   str | None]:
    """Build the exact unit evidence plan used by dry-run and staging."""
    direct_claims = [claim for _field, claim in unit_links]
    direct_claims += [item[1] for item in changes.values()
                      if item[1] is not None]
    if not direct_claims:
        return None, [], "no compatible claim remains after truth-gate pruning"
    basis, basis_derivation = _unit_basis(direct_claims)
    incompatible_changes = [
        (field, claim.get("field"))
        for field, item in changes.items()
        if (claim := item[1]) is not None
        if not _unit_receipt_compatible(basis, field, claim)
    ]
    if incompatible_changes:
        detail = ", ".join(
            f"{field} <- {claim_field}"
            for field, claim_field in incompatible_changes)
        return None, [], f"basis {basis} is incompatible with {detail}"
    compatible_links = [
        (field, claim) for field, claim in unit_links
        if _unit_receipt_compatible(basis, field, claim)
    ]
    skipped = [item for item in unit_links if item not in compatible_links]
    direct_claims = [claim for _field, claim in compatible_links]
    direct_claims += [item[1] for item in changes.values()
                      if item[1] is not None]
    verification, verification_derivation = _verification_state(direct_claims)
    try:
        representative = _basis_claim(direct_claims, basis)
    except ValueError as exc:
        return None, skipped, str(exc)
    return {
        "basis": basis,
        "basis_derivation": basis_derivation,
        "verification": verification,
        "verification_derivation": verification_derivation,
        "representative": representative,
        "unit_links": compatible_links,
    }, skipped, None


def _verification_state(claims: list[dict]) -> tuple[str, str]:
    documents = {claim.get("document_id") for claim in claims
                 if claim.get("document_id")}
    if len(documents) >= 2:
        return "corroborated", "two or more archived documents support this version"
    return "source_asserted", "one archived document supports this version"


def provenance_facility_map(provenance: list[dict], units: list[dict],
                            permits: list[dict]) -> dict[str, set[str]]:
    """Archived document -> facilities named by existing reviewed receipts."""
    owners = {
        ("unit", row["id"]): row["facility_id"] for row in units
    } | {
        ("permit", row["id"]): row["facility_id"] for row in permits
    }
    out: dict[str, set[str]] = {}
    for row in provenance:
        facility_id = owners.get((row.get("fact_table"), row.get("fact_id")))
        document_id = ((row.get("claim") or {}).get("document_id"))
        if facility_id and document_id:
            out.setdefault(document_id, set()).add(facility_id)
    return out


def fact_field_supported(table: str, fact: dict, field: str) -> bool:
    """Ask migration 008's semantic gate about an existing fact field."""
    value = fact.get(field)
    numeric = value if table == "unit" and field in {
        "unit_count", "mw_each", "total_mw", "hours_permitted"} else None
    result = _rest("POST", "rpc/btw_fact_field_supported", json={
        "p_fact_table": table,
        "p_fact_id": fact["id"],
        "p_fact_field": field,
        "p_value": None if value is None else str(value),
        "p_numeric_value": numeric,
    }).json()
    return result is True


def _make_context_of():
    """Claim -> sentence window around its quote in the archived page text.
    Lazy R2 + pypdf; one document fetched at most once. Returns None (and
    context binding silently stays off) when R2 credentials are absent."""
    if not (os.environ.get("R2_ACCESS_KEY") and os.environ.get("R2_SECRET")):
        return None
    import io
    from pypdf import PdfReader  # lazy: only needed when R2 creds present
    from btw_engine.fetch import s3, BUCKET

    def page_texts(pdf_bytes: bytes) -> list[str]:
        # local copy — importing btw_engine.extract would drag in the LLM
        # stack (litellm), which the daily job deliberately doesn't install
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return [(p.extract_text() or "") for p in reader.pages]

    r2_keys: dict[str, str] = {}
    pages_cache: dict[str, list[str]] = {}

    def context_of(claim: dict) -> str | None:
        doc_id = claim["document_id"]
        if doc_id not in r2_keys:
            rows = _rest("GET", "document", params={
                "select": "id,r2_key", "id": f"eq.{doc_id}"}).json()
            r2_keys[doc_id] = rows[0]["r2_key"] if rows else ""
        key = r2_keys[doc_id]
        if not key.endswith(".pdf"):
            return None
        if doc_id not in pages_cache:
            try:
                body = s3().get_object(Bucket=BUCKET, Key=key)["Body"].read()
                pages_cache[doc_id] = page_texts(body)
            except Exception as e:  # noqa: BLE001 — binding is best-effort
                print(f"WARN context fetch failed for doc {doc_id[:8]}: {e}")
                pages_cache[doc_id] = []
        pages = pages_cache[doc_id]
        page_no = claim.get("page") or 1
        if not pages or not 1 <= page_no <= len(pages):
            return None
        return locate_context(pages[page_no - 1], claim.get("quote") or "")

    return context_of


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    permits = _rest("GET", "permit", params={
        "select": "id,logical_id,permit_no,facility_id,authority,permit_type,"
                  "status,filed_at,issued_at,verification_state",
        "fact_state": "eq.published"}).json()
    all_units = _rest("GET", "unit", params={
        "select": "id,logical_id,facility_id,oem,model,unit_count,mw_each,"
                  "total_mw,fuel,hours_permitted,basis,verification_state",
        "fact_state": "eq.published"}).json()
    facilities = {f["id"]: f for f in _rest("GET", "facility", params={
        "select": "id,slug"}).json()}
    claims = _rest("GET", "claim", params={
        "select": "id,document_id,entity_hint,field,value,value_num,quote,page,"
                  "match_score,numeric_check,anchor,bbox,"
                  "document:document_id(doc_genre)",
        "status": "eq.validated"}).json()
    context_of = _make_context_of()
    if context_of is None:
        print("normalize: R2 creds absent — sentence-context binding off")

    by_doc: dict[str, list[dict]] = {}
    for c in claims:
        by_doc.setdefault(c["document_id"], []).append(c)

    prior_receipts = _rest("GET", "fact_provenance", params={
        "select": "fact_table,fact_id,claim:claim_id(document_id)"
    }).json()
    doc_facilities = provenance_facility_map(
        prior_receipts, all_units, permits)
    units_by_facility: dict[str, list[dict]] = {}
    permits_by_facility: dict[str, list[dict]] = {}
    for row in all_units:
        units_by_facility.setdefault(row["facility_id"], []).append(row)
    for row in permits:
        permits_by_facility.setdefault(row["facility_id"], []).append(row)

    facility_claims: dict[str, list[dict]] = {}
    permit_claims_by_id: dict[str, list[dict]] = {}
    for doc_id, doc_claims in sorted(by_doc.items()):
        permit = resolve_permit(doc_claims, permits)
        fac_id = (permit["facility_id"] if permit else resolve_facility(
            doc_claims, permits, doc_facilities.get(doc_id)))
        if fac_id is None:
            print(f"UNRESOLVED doc {doc_id[:8]}: no unique permit or "
                  f"reviewed provenance scope ({len(doc_claims)} claims held back)")
            continue
        facility_claims.setdefault(fac_id, []).extend(doc_claims)
        if permit is None:
            scoped_permits = permits_by_facility.get(fac_id, [])
            permit = scoped_permits[0] if len(scoped_permits) == 1 else None
        if permit is not None:
            permit_claims_by_id.setdefault(permit["id"], []).extend(doc_claims)

    n_links = n_stage = 0
    for fac_id, doc_claims in sorted(facility_claims.items()):
        slug = facilities[fac_id]["slug"]
        units = units_by_facility.get(fac_id, [])
        links, updates, conflicts = plan_unit_changes(
            units, doc_claims, context_of=context_of)
        for msg in conflicts:
            print(f"CONFLICT {slug}: {msg}")

        # Any non-null field that still fails migration 008's semantic gate is
        # removed from the new version unless this run has a compatible claim
        # to replace it. Unknown equipment attributes are nullable by design.
        linked_by_unit = {(u["id"], field) for u, field, _claim in links}
        for unit in units:
            chg = updates.setdefault(unit["id"], {})
            for field in ("oem", "model", "unit_count", "mw_each", "total_mw",
                          "fuel", "hours_permitted"):
                if (unit.get(field) is not None
                        and (unit["id"], field) not in linked_by_unit
                        and field not in chg
                        and not fact_field_supported("unit", unit, field)):
                    chg[field] = (None, None, "truth-gate prune")
            if not chg:
                updates.pop(unit["id"], None)

        # Dry-run and staging consume one evidence plan. This prevents a plan
        # from succeeding read-only and then choosing different receipts when
        # writes are enabled.
        touched_units = ({u["id"] for u, _field, _claim in links}
                         | set(updates))
        for uid in touched_units:
            u = next(x for x in units if x["id"] == uid)
            unit_links = [(field, claim) for linked, field, claim in links
                          if linked["id"] == uid]
            chg = updates.get(uid, {})
            plan, skipped, error = prepare_unit_receipts(unit_links, chg)
            prefix = "DRY " if args.dry_run else ""
            for field, claim in skipped:
                print(f"{prefix}SKIP {slug}/{u.get('model') or uid}.{field}: "
                      f"{claim.get('field')} is incompatible with "
                      f"basis {plan['basis'] if plan else 'candidate'}")
            if error:
                print(f"{prefix}HOLD {slug}/{u.get('model') or uid}: {error}")
                continue
            unit_links = plan["unit_links"]
            basis = plan["basis"]
            verification = plan["verification"]
            if args.dry_run:
                print(f"DRY CLASS {slug}/{u.get('model') or uid}: "
                      f"basis={basis}, verification={verification}")
                for field, c in unit_links:
                    print(f"DRY LINK  {slug}/{u.get('model') or uid}.{field} "
                          f"<- claim {c['id'][:8]}")
                for col, (new, _c, method) in chg.items():
                    print(f"DRY STAGE {slug}/{u.get('model') or uid}.{col}: "
                          f"{u.get(col)} -> {new} [{method}]")
                continue

            # Every evidence change creates/updates a staging version.
            # Published rows remain immutable until manifest promotion.
            sid = staged_copy(u, chg, basis=basis,
                              verification_state=verification)
            replaced = {field for field, _claim in unit_links} | set(chg)
            replaced |= {"basis", "verification_state"}
            copy_provenance("unit", u["id"], sid,
                            exclude_fields=replaced)
            for field, c in unit_links:
                if ensure_provenance(
                        "unit", sid, field, c["id"],
                        f"corroborated by anchored quote "
                        f"(match {c['match_score']})"):
                    n_links += 1
            for col, (new, c, method) in chg.items():
                if c is not None:
                    note = (f"staged update {u.get(col)} -> {new}"
                            + (" (bound via sentence context)"
                               if method == "context" else ""))
                    ensure_provenance("unit", sid, col, c["id"], note)
                n_stage += 1
                print(f"STAGE {slug}/{u.get('model') or uid}.{col}: "
                      f"{u.get(col)} -> {new} [{method}]")
            ensure_provenance(
                "unit", sid, "basis", plan["representative"]["id"],
                "engine classification", "derived",
                plan["basis_derivation"])
            ensure_provenance(
                "unit", sid, "verification_state",
                plan["representative"]["id"],
                "engine classification", "derived",
                plan["verification_derivation"])

        for permit in permits_by_facility.get(fac_id, []):
            permit_claims = permit_claims_by_id.get(permit["id"], [])
            permit_links, permit_updates, permit_conflicts = \
                plan_permit_changes(permit, permit_claims)
            for msg in permit_conflicts:
                print(f"CONFLICT {slug}: {msg}")
            covered = {field for _p, field, _claim, _method in permit_links}
            covered |= set(permit_updates)
            missing = [field for field in (
                "authority", "permit_no", "permit_type", "status",
                "filed_at", "issued_at")
                if permit.get(field) is not None and field not in covered
                and not fact_field_supported("permit", permit, field)]
            if args.dry_run:
                for p, field, c, method in permit_links:
                    print(f"DRY LINK  {slug}/permit {p['permit_no']}.{field} "
                          f"<- claim {c['id'][:8]} [{method}]")
                for col, (new, _claims, method) in permit_updates.items():
                    print(f"DRY STAGE {slug}/permit {permit['permit_no']}."
                          f"{col}: {permit.get(col)} -> {new} [{method}]")
                if missing:
                    print(f"DRY HOLD  {slug}/permit {permit['permit_no']}: "
                          f"missing compatible claims for {', '.join(missing)}")
                continue
            if missing:
                print(f"HOLD {slug}/permit {permit['permit_no']}: missing "
                      f"compatible claims for {', '.join(missing)}")
                continue
            if not permit_links and not permit_updates:
                continue
            permit_direct_claims = [claim for _p, _field, claim, _method
                                    in permit_links]
            permit_direct_claims += [claim for _value, claims_for_change, _method
                                     in permit_updates.values()
                                     for claim in claims_for_change]
            verification, derivation = _verification_state(
                permit_direct_claims)
            sid = staged_permit_copy(
                permit, permit_updates, verification_state=verification)
            replaced = covered | {"verification_state"}
            copy_provenance("permit", permit["id"], sid,
                            exclude_fields=replaced)
            for _p, field, c, method in permit_links:
                if ensure_provenance(
                        "permit", sid, field, c["id"],
                        f"corroborated by anchored quote ({method}; "
                        f"match {c['match_score']})"):
                    n_links += 1
            for field, (new, claims_for_change, method) in permit_updates.items():
                for c in claims_for_change:
                    ensure_provenance(
                        "permit", sid, field, c["id"],
                        f"staged update {permit.get(field)} -> {new} "
                        f"({method}; match {c['match_score']})")
                n_stage += 1
                print(f"STAGE {slug}/permit {permit['permit_no']}.{field}: "
                      f"{permit.get(field)} -> {new} [{method}]")
            representative = permit_direct_claims[0]
            ensure_provenance(
                "permit", sid, "verification_state", representative["id"],
                "engine classification", "derived", derivation)

    print(f"normalize: {n_links} new provenance links, "
          f"{n_stage} staged field updates")


if __name__ == "__main__":
    main()
