"""btw_engine.extract — anchored claim extraction from archived documents.

Architecture §4.4, anchoring v2 + quorum (M3.5, decisions D8/D9). Pipeline
per document:
R2 bytes → per-page text (pypdf) → LLM claims JSON (role: default_extractor,
overridable) → two-layer validation: (1) verbatim quote fuzzy-matched against
the page text (≥ 0.92 after normalization; quotes straddling a page break are
retried against the page±1 span), (2) numeric value re-located in the matched
text's numeric inventory. Only claims passing both can become 'validated'.

A claim the LLM invented cannot survive: its quote won't match, or its number
won't exist on the page. That is the anti-hallucination backbone.

Quorum (--quorum): an independent cross_checker (different model family, on
purpose) extracts the same document. Anchor-validated primary claims are then
judged: value confirmed by the checker → 'agree'; checker anchored the same
passage to a different value → 'disagree' (contradiction) → one escalation
pass; still contradicted → claim held at status 'extracted' for human review;
checker silent on the passage → 'solo' (anchor already passed, stays
validated). Verdicts land in claim.quorum (schema/005_claim_quorum.sql).

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, R2_*, provider key(s) for the roles
used; optional BTW_EXTRACTOR_ROLE overrides the primary role. Usage:
  python -m btw_engine.extract --genres tceq_standard_permit_review,appeal_filing,advocacy_letter
  python -m btw_engine.extract --sha <sha256> --quorum
"""

import argparse
import io
import json
import os
import re
import sys

import httpx
from pypdf import PdfReader
from rapidfuzz import fuzz

from btw_engine.fetch import s3, BUCKET
from btw_engine import llm

EXTRACT_BASE_VERSION = "extract-v2"
MATCH_THRESHOLD = 0.92
QUOTE_IDENTITY_THRESHOLD = 0.85  # two quotes anchor the same passage
MAX_CHARS_TO_LLM = 60_000

PROMPT = """You are extracting structured facts from a US government or legal \
document about power generation equipment serving data centers.

Return STRICT JSON, no markdown fences, exactly this shape:
{"claims": [{"entity_hint": "...", "field": "...", "value": "...", \
"value_num": 0, "unit": "...", "quote": "...", "page": 1}]}

Fields to extract WHEN PRESENT (skip absent ones):
facility.name, facility.operator, unit.oem, unit.model, unit.fuel, unit.count,
unit.mw_each, unit.mw_total, unit.hours_permitted, permit.no, permit.type,
permit.authority, permit.status, permit.filed_at, permit.issued_at,
observation.unit_count, observation.mw.

Hard rules:
- "quote" MUST be a verbatim span copied character-for-character from the
  page text, max 300 chars, containing the fact. Do not paraphrase.
- "page" is the page number where the quote appears, per the [PAGE n] markers.
- "value_num" is the numeric value for numeric facts, else null.
- "unit" examples: MW, count, hours/year. Null if not applicable.
- Use permit.filed_at only when the quote explicitly says filed, submitted,
  received, or application date. Use permit.issued_at only when it explicitly
  says issued, approved, effective, or permit date. Never emit an ambiguous
  generic permit date.
- JSON only. No commentary."""


def _rest(method: str, path: str, **kw) -> httpx.Response:
    base = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/" + path
    key = os.environ["SUPABASE_SERVICE_KEY"]
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    headers.update(kw.pop("headers", {}))
    r = httpx.request(method, base, headers=headers, timeout=60, **kw)
    r.raise_for_status()
    return r


def _norm(s: str) -> str:
    s = s.lower()
    s = s.replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip()


_NUM_RE = re.compile(r"\d[\d,]*\.?\d*")

# Regulatory prose spells small counts out ("five (5) turbines" or just
# "five turbines"). Without these, correct claims get numeric_check=false.
_WORD_NUMS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
}
_WORD_RE = re.compile(
    r"\b(" + "|".join(_WORD_NUMS) + r")\b", re.IGNORECASE)


def numeric_inventory(text: str) -> set[float]:
    out = set()
    for m in _NUM_RE.finditer(text):
        try:
            out.add(float(m.group().replace(",", "")))
        except ValueError:
            pass
    for m in _WORD_RE.finditer(text):
        out.add(float(_WORD_NUMS[m.group().lower()]))
    return out


def page_texts(pdf_bytes: bytes) -> list[str]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return [(p.extract_text() or "") for p in reader.pages]


def _candidate_texts(pages: list[str], page_no: int) -> list[tuple[int, str]]:
    """Anchor targets: the claimed page first, then the page-break spans with
    the previous and next page. Long filings break quotes across pages; the
    claimed page stays authoritative for provenance, the span only rescues
    the match. Returns [(page_offset, text)]."""
    out = [(0, pages[page_no - 1])]
    if page_no >= 2:
        out.append((-1, pages[page_no - 2] + "\n" + pages[page_no - 1]))
    if page_no < len(pages):
        out.append((+1, pages[page_no - 1] + "\n" + pages[page_no]))
    return out


def validate_claim(claim: dict, pages: list[str]
                   ) -> tuple[float, bool | None, int]:
    """Returns (match_score, numeric_check, page_offset). page_offset is 0
    when the quote matched the claimed page alone, ±1 when it only matched
    the span including the previous/next page (cross-page quote)."""
    page_no = claim.get("page") or 1
    if not 1 <= page_no <= len(pages):
        return 0.0, False, 0
    quote = claim.get("quote") or ""

    score, text, offset = 0.0, pages[page_no - 1], 0
    if quote:
        nq = _norm(quote)
        for off, cand in _candidate_texts(pages, page_no):
            s = fuzz.partial_ratio(nq, _norm(cand)) / 100
            if s > score:
                score, text, offset = s, cand, off
            if off == 0 and s >= MATCH_THRESHOLD:
                break  # claimed page suffices; skip span fallbacks

    num_ok: bool | None = None
    vn = claim.get("value_num")
    if vn is not None:
        try:
            vnf = float(vn)
            inv = numeric_inventory(text)
            num_ok = any(
                abs(n - vnf) <= max(0.001, abs(vnf) * 0.001) for n in inv)
        except (TypeError, ValueError):
            num_ok = False
    return score, num_ok, offset


def _parse_claims(raw: str) -> list[dict]:
    raw = raw.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.M).strip()
    try:
        return json.loads(raw, strict=False).get("claims", [])
    except json.JSONDecodeError:
        pass
    # Salvage a truncated/imperfect response: cut back to the last complete
    # claim object and close the JSON. LLM outputs sometimes die mid-string.
    ends = [m.start() for m in re.finditer(r"\}", raw)]
    for i in reversed(ends[-80:]):
        try:
            got = json.loads(raw[: i + 1] + "]}", strict=False)
            return got["claims"]
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    raise ValueError(f"unparseable LLM response ({len(raw)} chars)")


def _values_match(a: dict, b: dict) -> bool:
    """Same fact? Numeric claims compare value_num with 0.1% tolerance;
    textual claims compare normalized values by containment either way."""
    va, vb = a.get("value_num"), b.get("value_num")
    if va is not None and vb is not None:
        try:
            fa, fb = float(va), float(vb)
            return abs(fa - fb) <= max(0.001, abs(fa) * 0.001)
        except (TypeError, ValueError):
            return False
    na, nb = _norm(str(a.get("value") or "")), _norm(str(b.get("value") or ""))
    return bool(na and nb) and (na in nb or nb in na)


def _same_passage(a: dict, b: dict) -> bool:
    qa, qb = _norm(a.get("quote") or ""), _norm(b.get("quote") or "")
    if not qa or not qb:
        return False
    return fuzz.partial_ratio(qa, qb) / 100 >= QUOTE_IDENTITY_THRESHOLD


def quorum_verdicts(primary: list[dict], checker: list[dict]) -> list[str]:
    """Per D9, judged post-anchor-validation. For each primary claim:
    'agree'    — a checker claim with the same field confirms the value;
    'disagree' — a checker claim anchored to the SAME passage (quote overlap)
                 carries a different value: a true contradiction;
    'solo'     — the checker is silent on this fact. Absence is coverage
                 variance between models, not contradiction; the anchor has
                 already vouched for the claim."""
    verdicts = []
    for p in primary:
        same_field = [c for c in checker if c.get("field") == p.get("field")]
        if any(_values_match(p, c) for c in same_field):
            verdicts.append("agree")
        elif any(_same_passage(p, c) for c in same_field):
            verdicts.append("disagree")
        else:
            verdicts.append("solo")
    return verdicts


def _llm_claims(role: str, marked: str, doc_id: str) -> list[dict]:
    """One extraction pass for a role; retry once on unparseable output."""
    for attempt in (1, 2):
        raw = llm.complete(
            role,
            [{"role": "user",
              "content": PROMPT + "\n\nDOCUMENT:\n" + marked}],
            purpose="extract",
            document_id=doc_id,
            response_format={"type": "json_object"},
        )
        try:
            return _parse_claims(raw)
        except ValueError:
            if attempt == 2:
                raise
    return []  # unreachable


def _anchored(claims: list[dict], pages: list[str]) -> list[dict]:
    """Anchor-validated subset, for quorum comparison only (not stored)."""
    out = []
    for c in claims:
        score, num_ok, _off = validate_claim(c, pages)
        if score >= MATCH_THRESHOLD and num_ok is not False:
            out.append(c)
    return out


def extractor_version(role: str, quorum: bool) -> str:
    model = llm.load_roles()[role]["model"]
    return (f"{EXTRACT_BASE_VERSION}"
            f"{'+quorum' if quorum else ''}@{model}")


def extract_document(doc: dict, role: str = "default_extractor",
                     quorum: bool = False) -> dict:
    body = s3().get_object(Bucket=BUCKET, Key=doc["r2_key"])["Body"].read()
    pages = page_texts(body)
    marked = "\n\n".join(f"[PAGE {i+1}]\n{t}" for i, t in enumerate(pages))
    if len(marked) > MAX_CHARS_TO_LLM:
        marked = marked[:MAX_CHARS_TO_LLM]

    claims = _llm_claims(role, marked, doc["id"])

    # Anchor validation (layer 1+2) for every primary claim.
    anchored_flags: list[tuple[float, bool | None, int]] = [
        validate_claim(c, pages) for c in claims]

    # Quorum (D9): independent pass by a different family; verdicts only —
    # checker/escalation claims are never stored, their cost hits the ledger.
    verdicts: list[str | None] = [None] * len(claims)
    if quorum:
        checker = _anchored(
            _llm_claims("cross_checker", marked, doc["id"]), pages)
        anchored_primary = [
            c for c, (s, n, _o) in zip(claims, anchored_flags)
            if s >= MATCH_THRESHOLD and n is not False]
        v_map = dict(zip(map(id, anchored_primary),
                         quorum_verdicts(anchored_primary, checker)))
        if "disagree" in v_map.values():
            esc = _anchored(
                _llm_claims("escalation", marked, doc["id"]), pages)
            disputed = [c for c in anchored_primary
                        if v_map[id(c)] == "disagree"]
            for c, v in zip(disputed, quorum_verdicts(disputed, esc)):
                v_map[id(c)] = "escalated" if v == "agree" else "disagree"
        verdicts = [v_map.get(id(c)) for c in claims]

    version = extractor_version(role, quorum)
    rows, validated, held = [], 0, 0
    for c, (score, num_ok, offset), verdict in zip(
            claims, anchored_flags, verdicts):
        anchor_ok = score >= MATCH_THRESHOLD and num_ok is not False
        if not anchor_ok:
            status = "rejected"
        elif verdict == "disagree":
            status = "extracted"  # contradiction survived escalation → human
            held += 1
        else:
            status = "validated"
            validated += 1
        row = {
            "document_id": doc["id"],
            "entity_hint": c.get("entity_hint"),
            "field": str(c.get("field"))[:120],
            "value": str(c.get("value"))[:2000],
            "value_num": c.get("value_num"),
            "unit": c.get("unit"),
            "anchor": "quote",
            "quote": str(c.get("quote"))[:2000],
            "page": c.get("page"),
            "match_score": round(score, 3),
            "numeric_check": num_ok,
            "confidence": round(score, 3),
            "extractor_version": version,
            "status": status,
        }
        if offset:
            row["entity_hint"] = ((c.get("entity_hint") or "") +
                                  f" [quote spans page break {offset:+d}]"
                                  ).strip()
        if quorum:
            row["quorum"] = verdict  # requires schema/005_claim_quorum.sql
        rows.append(row)
    if rows:
        _rest("POST", "claim", json=rows)
    return {"claims": len(rows), "validated": validated, "held": held,
            "pages": len(pages)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sha")
    ap.add_argument("--genres",
                    help="comma list of doc_genre values to extract")
    ap.add_argument("--role",
                    default=os.environ.get("BTW_EXTRACTOR_ROLE",
                                           "default_extractor"),
                    help="models.yaml role for the primary pass "
                         "(per-model evals, D8)")
    ap.add_argument("--quorum", action="store_true",
                    default=os.environ.get("BTW_QUORUM") == "1",
                    help="cross-check with a second family per D9")
    args = ap.parse_args()

    params = {"select": "id,sha256,r2_key,doc_genre,url"}
    if args.sha:
        params["sha256"] = f"eq.{args.sha}"
    elif args.genres:
        params["doc_genre"] = f"in.({args.genres})"
    else:
        ap.error("need --sha or --genres")

    docs = _rest("GET", "document", params=params).json()
    if not docs:
        sys.exit("no matching documents")

    failures = 0
    for doc in docs:
        if not doc["r2_key"].endswith(".pdf"):
            print(f"SKIP {doc['sha256'][:12]}: not a pdf ({doc['r2_key']})")
            continue
        try:
            stats = extract_document(doc, role=args.role, quorum=args.quorum)
            extra = (f", {stats['held']} held for review"
                     if stats["held"] else "")
            print(f"OK   {doc['sha256'][:12]} [{doc['doc_genre']}]: "
                  f"{stats['validated']}/{stats['claims']} claims validated"
                  f"{extra} ({stats['pages']} pages)")
        except Exception as e:  # noqa: BLE001 — batch continues
            failures += 1
            print(f"FAIL {doc['sha256'][:12]}: {e}")
    if failures:
        sys.exit(f"{failures} of {len(docs)} documents failed")


if __name__ == "__main__":
    main()
