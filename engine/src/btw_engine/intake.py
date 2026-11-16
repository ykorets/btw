"""btw_engine.intake — archive and extract TCEQ documents for watched filings.

For every relevant TCEQ registry filing (see publish.build_filings), look up
its permit number in TCEQ Records Online, archive documents that can carry
equipment or permit facts (fetch.py: sha256 -> R2 -> document row), and
extract anchored claims from each new document:

- deterministic regulatory parsers always run (no LLM, exact sentences);
- the LLM extractor runs with the quorum cross-check when provider keys are
  present; a contradiction it cannot resolve stays unvalidated.

bootstrap.py turns the validated claims into new facilities; normalize.py
keeps updating facilities that already exist.

Env: BTW_API_URL, BTW_API_KEY, BTW_ACCESS_CLIENT_ID/SECRET, R2_*; optional
GEMINI_API_KEY + ANTHROPIC_API_KEY for the LLM pass.
Usage: python -m btw_engine.intake [--max-new 30] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import time

from btw_engine import fetch
from btw_engine.bootstrap import tceq_filings
from btw_engine.normalize import _rest
from btw_engine.tceq_records import RecordsClient

SOURCE_ID = "tceq-permit-portal"
GENRE_PRIORITY = (
    "tceq_special_conditions", "tceq_preliminary_determination",
    "tceq_standard_permit_final_action", "tceq_maert",
    "tceq_standard_permit_review", "tceq_nsr_application",
)


def plan_downloads(found: list[tuple[dict, dict]], known_urls: set[str],
                   limit: int) -> list[tuple[dict, dict]]:
    """New useful documents, best evidence first, at most ``limit``."""
    out, seen = [], set()
    for filing, doc in found:
        if not doc.get("genre") or doc["url"] in known_urls:
            continue
        if doc["did"] in seen:
            continue
        seen.add(doc["did"])
        out.append((filing, doc))
    out.sort(key=lambda item: GENRE_PRIORITY.index(item[1]["genre"]))
    return out[:limit]


RETRYABLE = ("RateLimit", "ServiceUnavailable", "overloaded", "429", "503")


def with_retry(fn, *args, attempts: int = 4, wait: float = 45.0, **kwargs):
    """Provider rate limits are routine on a 30-document batch: back off."""
    for attempt in range(attempts):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — classify by message
            if attempt == attempts - 1 or not any(
                    marker in str(exc) for marker in RETRYABLE):
                raise
            time.sleep(wait * (attempt + 1))
    raise AssertionError("unreachable")


def unextracted_documents(limit: int) -> list[dict]:
    """Archived TCEQ documents with no claims yet (an earlier extraction
    failed, e.g. a provider rate limit). They are retried before new ones."""
    docs = _rest("GET", "document", params={
        "select": "id,sha256,r2_key,doc_genre,url",
        "source_id": f"eq.{SOURCE_ID}",
        "doc_genre": f"in.({','.join(GENRE_PRIORITY)})",
        "order": "fetched_at.asc"}).json()
    if not docs:
        return []
    with_claims = set()
    ids = [d["id"] for d in docs]
    for i in range(0, len(ids), 100):
        rows = _rest("GET", "claim", params={
            "select": "document_id",
            "document_id": f"in.({','.join(ids[i:i + 100])})"}).json()
        with_claims |= {r["document_id"] for r in rows}
    return [d for d in docs if d["id"] not in with_claims][:limit]


def llm_available() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY")
                and os.environ.get("ANTHROPIC_API_KEY"))


def run(max_new: int = 30, dry_run: bool = False) -> dict:
    from btw_engine.extract import (
        extract_deterministic_document, extract_document)
    from btw_engine.publish import build_filings, fetch_filing_candidates

    filings = tceq_filings(build_filings(fetch_filing_candidates()))
    known = {row["url"] for row in _rest("GET", "document", params={
        "select": "url", "source_id": f"eq.{SOURCE_ID}"}).json()}
    records = RecordsClient()
    found, lookups_failed = [], 0
    for filing in filings:
        try:
            docs = records.search(filing["record_no"])
        except Exception as exc:  # noqa: BLE001 — one permit must not stop the sweep
            lookups_failed += 1
            print(f"FAIL lookup {filing['record_no']}: {exc}")
            continue
        useful = [d for d in docs if d.get("genre")]
        print(f"     {filing['record_no']} {filing['name']}: "
              f"{len(docs)} documents, {len(useful)} useful")
        found.extend((filing, doc) for doc in docs)

    todo = plan_downloads(found, known, max_new)
    stats = {"filings": len(filings), "lookups_failed": lookups_failed,
             "new_documents": len(todo), "archived": 0, "extracted": 0,
             "failed": 0}
    if dry_run:
        for filing, doc in todo:
            print(f"DRY  {filing['record_no']} {doc['title']} {doc['date']}: {doc['url']}")
        print(stats)
        return stats

    s3 = fetch.s3()
    use_llm = llm_available()
    if not use_llm:
        print("intake: LLM keys absent — deterministic extraction only")

    def extract(row: dict) -> dict:
        if row["r2_key"].endswith(".pdf") and use_llm:
            return with_retry(extract_document, row, quorum=True)
        return extract_deterministic_document(row)

    retry = unextracted_documents(max_new)
    stats["retried"] = 0
    for row in retry:
        try:
            result = extract(row)
            stats["retried"] += 1
            print(f"OK   retry {row['sha256'][:12]} [{row['doc_genre']}]: "
                  f"{result['validated']}/{result['claims']} claims validated")
        except Exception as exc:  # noqa: BLE001 — batch continues
            stats["failed"] += 1
            print(f"FAIL retry {row['sha256'][:12]}: {exc}")

    for filing, doc in todo[:max(0, max_new - len(retry))]:
        label = f"{filing['record_no']} {doc['title']} ({doc['date']})"
        try:
            sha, note = fetch.fetch_one(s3, doc["url"], SOURCE_ID, doc["genre"])
            stats["archived"] += 1
            if note == "cached":
                # The same file is filed under each permit number of the
                # project (NSR, PSD, GHG): extract it once.
                print(f"SAME {label}: {sha[:12]} already archived")
                continue
            row = _rest("GET", "document", params={
                "select": "id,sha256,r2_key,doc_genre,url",
                "sha256": f"eq.{sha}"}).json()[0]
            result = extract(row)
            stats["extracted"] += 1
            print(f"OK   {label}: {sha[:12]} ({note}); "
                  f"{result['validated']}/{result['claims']} claims validated")
        except Exception as exc:  # noqa: BLE001 — batch continues
            stats["failed"] += 1
            print(f"FAIL {label}: {exc}")
    print(f"intake: {stats}")
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-new", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(max_new=args.max_new, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
