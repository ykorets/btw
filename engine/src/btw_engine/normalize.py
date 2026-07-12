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
- Bound claim, value == published  → corroboration: fact_provenance link
  (this replaces the M1 manual provenance stubs with anchored claims).
- Bound claim, value != published  → staged update: a fact_state='staging'
  copy of the unit carrying the new value, with provenance rows per changed
  field. The review PR shows the diff; review.py --promote flips it live.
- Unresolved documents and unbound claims are reported, never guessed.

Usage: python -m btw_engine.normalize [--dry-run]
Env: SUPABASE_URL, SUPABASE_SERVICE_KEY; R2_* optional — without R2 access
context binding is skipped and quote-token binding still works.
"""

import argparse
import os
import re

import httpx
from rapidfuzz import fuzz

CONTEXT_RADIUS = 240          # chars around the located quote
CONTEXT_LOCATE_THRESHOLD = 85  # partial_ratio_alignment score to trust locate

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
    tokens = [_norm(u["model"]) for u in units]

    def bind(u, c, col, method):
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
                updates[u["id"]][col] = (new, c, method)

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
            bind(u, c, col, "quote")

    # Second pass: claims whose quote names NO model token at all — try the
    # sentence context around the quote in the source page (M3.5).
    if context_of is not None:
        for c in doc_claims:
            col = FIELD_COLS.get(c["field"])
            if col is None or c.get("value_num") is None:
                continue
            nq = _norm(c.get("quote") or "")
            if any(t and t in nq for t in tokens):
                continue  # already handled by quote-token binding
            ctx = context_of(c)
            if not ctx:
                continue
            u = _sole_context_unit(units, ctx)
            if u is not None:
                bind(u, c, col, "context")
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
             for col, (new, *_rest_) in changes.items()}
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


def _make_context_of():
    """Claim -> sentence window around its quote in the archived page text.
    Lazy R2 + pypdf; one document fetched at most once. Returns None (and
    context binding silently stays off) when R2 credentials are absent."""
    if not (os.environ.get("R2_ACCESS_KEY") and os.environ.get("R2_SECRET")):
        return None
    from btw_engine.extract import page_texts  # pypdf import stays lazy
    from btw_engine.fetch import s3, BUCKET

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
        "select": "id,permit_no,facility_id"}).json()
    facilities = {f["id"]: f for f in _rest("GET", "facility", params={
        "select": "id,slug"}).json()}
    claims = _rest("GET", "claim", params={
        "select": "id,document_id,field,value,value_num,quote,page,"
                  "match_score",
        "status": "eq.validated"}).json()
    context_of = _make_context_of()
    if context_of is None:
        print("normalize: R2 creds absent — sentence-context binding off")

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
        links, updates, conflicts = plan_unit_changes(
            units, doc_claims, context_of=context_of)
        for msg in conflicts:
            print(f"CONFLICT {slug}: {msg}")
        if args.dry_run:
            for u, field, c in links:
                print(f"DRY LINK  {slug}/{u['model']}.{field} <- claim "
                      f"{c['id'][:8]}")
            for uid, chg in updates.items():
                u = next(x for x in units if x["id"] == uid)
                for col, (new, _c, method) in chg.items():
                    print(f"DRY STAGE {slug}/{u['model']}.{col}: "
                          f"{u.get(col)} -> {new} [{method}]")
            continue
        for u, field, c in links:
            if ensure_provenance("unit", u["id"], field, c["id"],
                                 f"corroborated by anchored quote "
                                 f"(match {c['match_score']})"):
                n_links += 1
        for uid, chg in updates.items():
            u = next(x for x in units if x["id"] == uid)
            sid = staged_copy(u, chg)
            for col, (new, c, method) in chg.items():
                note = (f"staged update {u.get(col)} -> {new}"
                        + (" (bound via sentence context)"
                           if method == "context" else ""))
                ensure_provenance("unit", sid, col, c["id"], note)
                n_stage += 1
                print(f"STAGE {slug}/{u['model']}.{col}: "
                      f"{u.get(col)} -> {new} [{method}]")
    print(f"normalize: {n_links} new provenance links, "
          f"{n_stage} staged field updates")


if __name__ == "__main__":
    main()
