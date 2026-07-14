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
import html
import io
import json
import os
import re
import sys

import httpx
from pypdf import PdfReader
from rapidfuzz import fuzz

from btw_engine.fetch import s3, BUCKET

EXTRACT_BASE_VERSION = "extract-v2"
MATCH_THRESHOLD = 0.92
QUOTE_IDENTITY_THRESHOLD = 0.85  # two quotes anchor the same passage
MAX_CHARS_TO_LLM = 60_000
DETERMINISTIC_VERSION = "deterministic-regulatory-v1"

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


def html_text(body: bytes) -> str:
    """Visible text from an archived registry page, without live HTTP.

    The archive remains the only downstream read path.  Whitespace is
    collapsed after scripts, styles, comments, and tags are removed so the
    resulting text can use the same fuzzy quote validator as PDF pages.
    """
    source = body.decode("utf-8", errors="replace")
    source = re.sub(r"<(script|style)\b.*?</\1>", " ", source,
                    flags=re.S | re.I)
    source = re.sub(r"<!--.*?-->", " ", source, flags=re.S)
    source = re.sub(r"<[^>]+>", " ", source)
    return re.sub(r"\s+", " ", html.unescape(source)).strip()


def document_pages(body: bytes, r2_key: str) -> list[str]:
    if r2_key.lower().endswith(".pdf"):
        return page_texts(body)
    if r2_key.lower().endswith((".html", ".htm")):
        return [html_text(body)]
    return []


def deterministic_claims(pages: list[str]) -> list[dict]:
    """Exact regulatory phrases -> controlled permit claims.

    These rules classify explicit agency language; they do not infer from a
    hostname, document genre, or project name.  Every emitted value retains
    the exact matched quote and still passes ``validate_claim`` before it can
    become validated evidence.
    """
    out: list[dict] = []
    seen: set[tuple] = set()

    def emit(*, page: int, quote: str, field: str, value: str,
             permit_no: str | None = None) -> None:
        quote = quote.strip()
        # Repeated permit headers appear on every PDF page. One anchored
        # claim per exact phrase is sufficient and keeps review receipts
        # compact; retain the first page where the phrase occurs.
        key = (field, _norm(value), _norm(quote))
        if not quote or key in seen:
            return
        seen.add(key)
        out.append({
            "entity_hint": (f"permit {permit_no}" if permit_no else "permit"),
            "field": field,
            "value": value,
            "value_num": None,
            "unit": None,
            "quote": quote,
            "page": page,
        })

    permit_no_patterns = (
        r"(?:Standard Permit Registration (?:Number|No\.)|"
        r"Registration (?:Number|No\.))\s*:?\s*"
        r"([0-9]{4,})",
        r"(?:PSD Air Construction |Construction |Air )?Permit No\.?\s*:?\s*"
        r"([0-9A-Z][0-9A-Z-]{4,})",
    )

    for page_no, text in enumerate(pages, start=1):
        page_numbers: list[str] = []
        for pattern in permit_no_patterns:
            for match in re.finditer(pattern, text, flags=re.I):
                permit_no = match.group(1)
                page_numbers.append(permit_no)
                emit(page=page_no, quote=match.group(0), field="permit.no",
                     value=permit_no, permit_no=permit_no)
        unique_numbers = list(dict.fromkeys(page_numbers))
        sole_no = unique_numbers[0] if len(unique_numbers) == 1 else None

        for match in re.finditer(
                r"Texas\s+Commission\s+on\s+Environmental\s+Quality\s*"
                r"\(TCEQ\)",
                text, flags=re.I):
            emit(page=page_no, quote=match.group(0),
                 field="permit.authority", value="TCEQ", permit_no=sole_no)

        for match in re.finditer(
                r"Shelby\s+County\s+Health\s+Department(?:[’']s)?\s*"
                r"\([“\"]SCHD[”\"]\s+or\s+[“\"]Health Department[”\"]\)",
                text, flags=re.I):
            emit(page=page_no, quote=match.group(0),
                 field="permit.authority",
                 value="Shelby County Health Department", permit_no=sole_no)

        for match in re.finditer(
                r"Electric Generating Unit(?:s)?(?: Air Quality)? Standard Permit",
                text, flags=re.I):
            emit(page=page_no, quote=match.group(0), field="permit.type",
                 value="EGU Standard Permit registration", permit_no=sole_no)

        for match in re.finditer(
                r"(?:Draft\s+)?Construction Air Permit No\.?\s*:?\s*"
                r"([0-9A-Z][0-9A-Z-]{4,})", text, flags=re.I):
            permit_no = match.group(1)
            emit(page=page_no, quote=match.group(0), field="permit.type",
                 value="Air construction permit", permit_no=permit_no)

        for match in re.finditer(
                r"PSD Air Construction Permit No\.?\s*:?\s*"
                r"([0-9A-Z][0-9A-Z-]{4,})", text, flags=re.I):
            permit_no = match.group(1)
            emit(page=page_no, quote=match.group(0), field="permit.type",
                 value="PSD Air Construction Permit", permit_no=permit_no)

        # MDEQ's HTML registry uses the inverse controlled label.
        for match in re.finditer(
                r"Air\s*-\s*Air-Construction PSD\s+"
                r"([0-9A-Z][0-9A-Z-]{4,})\s+"
                r"(\d{1,2}/\d{1,2}/\d{4})", text, flags=re.I):
            permit_no, issued_at = match.groups()
            # The registry row has no ``Permit No.`` label, so it does not
            # match the generic number patterns above.  Emit the explicit
            # row key as a typed claim; without it the document cannot be
            # deterministically scoped to the known permit.
            emit(page=page_no, quote=match.group(0), field="permit.no",
                 value=permit_no, permit_no=permit_no)
            emit(page=page_no, quote=match.group(0), field="permit.type",
                 value="PSD Air Construction Permit", permit_no=permit_no)
            emit(page=page_no, quote=match.group(0), field="permit.issued_at",
                 value=issued_at, permit_no=permit_no)

        for match in re.finditer(
                r"currently permitted under Standard Permit Registration\s+"
                r"([0-9]{4,})", text, flags=re.I):
            permit_no = match.group(1)
            emit(page=page_no, quote=match.group(0), field="permit.status",
                 value="issued", permit_no=permit_no)

        for match in re.finditer(
                r"Appeal of Air Permit No\.?\s*([0-9A-Z][0-9A-Z-]{4,})",
                text, flags=re.I):
            permit_no = match.group(1)
            emit(page=page_no, quote=match.group(0), field="permit.status",
                 value="under appeal", permit_no=permit_no)

        for match in re.finditer(
                r"issuance of Air Permit No\.?\s*([0-9A-Z][0-9A-Z-]{4,})",
                text, flags=re.I):
            permit_no = match.group(1)
            emit(page=page_no, quote=match.group(0), field="permit.status",
                 value="issued", permit_no=permit_no)

        for match in re.finditer(
                r"issued\s+the\s+CTC\s+Permit\s+on\s+"
                r"([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", text):
            emit(page=page_no, quote=match.group(0),
                 field="permit.issued_at", value=match.group(1),
                 permit_no=sole_no)

        for match in re.finditer(
                r"MDEQ\s+issued\s+MZX\s+a\s+permit\s+on\s+"
                r"([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", text):
            issued_at = match.group(1)
            emit(page=page_no, quote=match.group(0), field="permit.status",
                 value="issued", permit_no=sole_no)
            emit(page=page_no, quote=match.group(0),
                 field="permit.issued_at", value=issued_at,
                 permit_no=sole_no)

        for match in re.finditer(
                r"([0-9A-Z][0-9A-Z-]{4,})\s+"
                r"(\d{1,2}/\d{1,2}/\d{4})\s+Permit Issued",
                text, flags=re.I):
            permit_no, issued_at = match.groups()
            emit(page=page_no, quote=match.group(0), field="permit.status",
                 value="issued", permit_no=permit_no)
            emit(page=page_no, quote=match.group(0),
                 field="permit.issued_at", value=issued_at,
                 permit_no=permit_no)
    return out


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


def canonical_claims(claims: list[dict]) -> list[dict]:
    """Add deterministic typed claims from explicit anchored language.

    This is deliberately narrower than extraction: it never invents a value
    and reuses the same verbatim quote/page.  It repairs two recurring legacy
    classifications before validation: ambiguous ``permit.date`` output when
    the quote itself says issued/filed, and fuel omitted from an explicit
    ``gas-fired`` equipment sentence.
    """
    out = list(claims)
    seen = {(
        c.get("field"), _norm(str(c.get("value") or "")),
        _norm(str(c.get("quote") or "")), c.get("page")
    ) for c in out}

    def append(source: dict, field: str, value: str,
               value_num=None, unit=None) -> None:
        key = (field, _norm(value), _norm(source.get("quote") or ""),
               source.get("page"))
        if key in seen:
            return
        row = dict(source)
        row.update({"field": field, "value": value,
                    "value_num": value_num, "unit": unit})
        out.append(row)
        seen.add(key)

    for claim in claims:
        quote = _norm(str(claim.get("quote") or ""))
        if claim.get("field") == "permit.date":
            if re.search(r"\b(issued|approved|effective)\b", quote):
                append(claim, "permit.issued_at", str(claim.get("value") or ""))
            elif re.search(r"\b(filed|submitted|received)\b", quote):
                append(claim, "permit.filed_at", str(claim.get("value") or ""))
        if (str(claim.get("field") or "").startswith(
                ("unit.", "observation."))
                and re.search(r"\b(?:natural\s+)?gas-fired\b", quote)):
            append(claim, "unit.fuel", "natural gas")
    return out


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
    from btw_engine import llm

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
    from btw_engine import llm

    model = llm.load_roles()[role]["model"]
    return (f"{EXTRACT_BASE_VERSION}"
            f"{'+quorum' if quorum else ''}@{model}")


def extract_deterministic_document(doc: dict, *, body: bytes | None = None,
                                   pages: list[str] | None = None) -> dict:
    """Persist idempotent, anchor-validated regulatory claims."""
    if body is None:
        body = s3().get_object(Bucket=BUCKET, Key=doc["r2_key"])["Body"].read()
    if pages is None:
        pages = document_pages(body, doc["r2_key"])
    if not pages:
        return {"claims": 0, "validated": 0, "held": 0, "pages": 0}

    existing = _rest("GET", "claim", params={
        "select": "field,value,quote,page,extractor_version,status",
        "document_id": f"eq.{doc['id']}",
        "extractor_version": f"eq.{DETERMINISTIC_VERSION}",
    }).json()
    signatures = {(
        row.get("field"), _norm(str(row.get("value") or "")),
        _norm(str(row.get("quote") or "")), row.get("page"),
    ) for row in existing}

    rows = []
    for claim in deterministic_claims(pages):
        signature = (
            claim["field"], _norm(str(claim["value"])),
            _norm(str(claim["quote"])), claim["page"],
        )
        if signature in signatures:
            continue
        score, num_ok, offset = validate_claim(claim, pages)
        if score < MATCH_THRESHOLD or num_ok is False:
            continue
        entity_hint = claim.get("entity_hint")
        if offset:
            entity_hint = ((entity_hint or "")
                           + f" [quote spans page break {offset:+d}]").strip()
        rows.append({
            "document_id": doc["id"],
            "entity_hint": entity_hint,
            "field": claim["field"],
            "value": claim["value"],
            "value_num": claim.get("value_num"),
            "unit": claim.get("unit"),
            "anchor": "quote",
            "quote": claim["quote"],
            "page": claim["page"],
            "match_score": round(score, 3),
            "numeric_check": num_ok,
            "confidence": round(score, 3),
            "extractor_version": DETERMINISTIC_VERSION,
            "status": "validated",
        })
        signatures.add(signature)
    if rows:
        _rest("POST", "claim", json=rows)
    return {"claims": len(rows), "validated": len(rows), "held": 0,
            "pages": len(pages)}


def extract_document(doc: dict, role: str = "default_extractor",
                     quorum: bool = False) -> dict:
    body = s3().get_object(Bucket=BUCKET, Key=doc["r2_key"])["Body"].read()
    pages = document_pages(body, doc["r2_key"])
    deterministic = extract_deterministic_document(
        doc, body=body, pages=pages)
    marked = "\n\n".join(f"[PAGE {i+1}]\n{t}" for i, t in enumerate(pages))
    if len(marked) > MAX_CHARS_TO_LLM:
        marked = marked[:MAX_CHARS_TO_LLM]

    claims = canonical_claims(_llm_claims(role, marked, doc["id"]))

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
            "pages": len(pages),
            "deterministic": deterministic["validated"]}


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
    ap.add_argument("--deterministic-only", action="store_true",
                    help="run exact regulatory parsers without an LLM call")
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
        supported = doc["r2_key"].lower().endswith((".pdf", ".html", ".htm"))
        if not supported:
            print(f"SKIP {doc['sha256'][:12]}: unsupported archive object "
                  f"({doc['r2_key']})")
            continue
        try:
            if args.deterministic_only or not doc["r2_key"].endswith(".pdf"):
                stats = extract_deterministic_document(doc)
            else:
                stats = extract_document(
                    doc, role=args.role, quorum=args.quorum)
            extra = (f", {stats['held']} held for review"
                     if stats["held"] else "")
            deterministic = stats.get("deterministic", stats["validated"])
            print(f"OK   {doc['sha256'][:12]} [{doc['doc_genre']}]: "
                  f"{stats['validated']}/{stats['claims']} claims validated"
                  f"{extra}; {deterministic} deterministic "
                  f"({stats['pages']} pages)")
        except Exception as e:  # noqa: BLE001 — batch continues
            failures += 1
            print(f"FAIL {doc['sha256'][:12]}: {e}")
    if failures:
        sys.exit(f"{failures} of {len(docs)} documents failed")


if __name__ == "__main__":
    main()
