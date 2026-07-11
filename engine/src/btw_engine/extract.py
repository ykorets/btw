"""btw_engine.extract — anchored claim extraction from archived documents.

Architecture §4.4, anchoring v2. Pipeline per document:
R2 bytes → per-page text (pypdf) → LLM claims JSON (role: default_extractor)
→ two-layer validation: (1) verbatim quote fuzzy-matched against the page
text (≥ 0.92 after normalization), (2) numeric value re-located in the page's
numeric inventory. Only claims passing both become status='validated'.

A claim the LLM invented cannot survive: its quote won't match, or its number
won't exist on the page. That is the anti-hallucination backbone.

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, R2_*, provider key for the extractor
role. Usage:
  python -m btw_engine.extract --genres tceq_standard_permit_review,appeal_filing,advocacy_letter
  python -m btw_engine.extract --sha <sha256>
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

EXTRACTOR_VERSION = "extract-v1@quorumless"
MATCH_THRESHOLD = 0.92
MAX_CHARS_TO_LLM = 60_000

PROMPT = """You are extracting structured facts from a US government or legal \
document about power generation equipment serving data centers.

Return STRICT JSON, no markdown fences, exactly this shape:
{"claims": [{"entity_hint": "...", "field": "...", "value": "...", \
"value_num": 0, "unit": "...", "quote": "...", "page": 1}]}

Fields to extract WHEN PRESENT (skip absent ones):
facility.name, facility.operator, unit.oem, unit.model, unit.count,
unit.mw_each, unit.mw_total, unit.hours_permitted, permit.no,
permit.authority, permit.status, permit.date, observation.unit_count,
observation.mw.

Hard rules:
- "quote" MUST be a verbatim span copied character-for-character from the
  page text, max 300 chars, containing the fact. Do not paraphrase.
- "page" is the page number where the quote appears, per the [PAGE n] markers.
- "value_num" is the numeric value for numeric facts, else null.
- "unit" examples: MW, count, hours/year. Null if not applicable.
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


def numeric_inventory(text: str) -> set[float]:
    out = set()
    for m in _NUM_RE.finditer(text):
        try:
            out.add(float(m.group().replace(",", "")))
        except ValueError:
            pass
    return out


def page_texts(pdf_bytes: bytes) -> list[str]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return [(p.extract_text() or "") for p in reader.pages]


def validate_claim(claim: dict, pages: list[str]) -> tuple[float, bool | None]:
    page_no = claim.get("page") or 1
    if not 1 <= page_no <= len(pages):
        return 0.0, False
    text = pages[page_no - 1]
    quote = claim.get("quote") or ""
    score = fuzz.partial_ratio(_norm(quote), _norm(text)) / 100 if quote else 0.0

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
    return score, num_ok


def extract_document(doc: dict) -> dict:
    body = s3().get_object(Bucket=BUCKET, Key=doc["r2_key"])["Body"].read()
    pages = page_texts(body)
    marked = "\n\n".join(f"[PAGE {i+1}]\n{t}" for i, t in enumerate(pages))
    if len(marked) > MAX_CHARS_TO_LLM:
        marked = marked[:MAX_CHARS_TO_LLM]

    raw = llm.complete(
        "default_extractor",
        [{"role": "user",
          "content": PROMPT + "\n\nDOCUMENT:\n" + marked}],
        purpose="extract",
        document_id=doc["id"],
    )
    raw = raw.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.M).strip()
    claims = json.loads(raw).get("claims", [])

    rows, validated = [], 0
    for c in claims:
        score, num_ok = validate_claim(c, pages)
        ok = score >= MATCH_THRESHOLD and num_ok is not False
        validated += ok
        rows.append({
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
            "extractor_version": EXTRACTOR_VERSION,
            "status": "validated" if ok else "rejected",
        })
    if rows:
        _rest("POST", "claim", json=rows)
    return {"claims": len(rows), "validated": validated,
            "pages": len(pages)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sha")
    ap.add_argument("--genres",
                    help="comma list of doc_genre values to extract")
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
            stats = extract_document(doc)
            print(f"OK   {doc['sha256'][:12]} [{doc['doc_genre']}]: "
                  f"{stats['validated']}/{stats['claims']} claims validated "
                  f"({stats['pages']} pages)")
        except Exception as e:  # noqa: BLE001 — batch continues
            failures += 1
            print(f"FAIL {doc['sha256'][:12]}: {e}")
    if failures:
        sys.exit(f"{failures} of {len(docs)} documents failed")


if __name__ == "__main__":
    main()
