"""Capture public OPSB PDFs after DIS's normal browser redirects complete.

An initial HTTP 200 HTML response is not the file. Use one ordinary browser
session and the site's public controls, waiting for the final download. The
only profile preference makes Chromium download PDFs rather than display them.
No credentials, fingerprint changes, proxy or interactive challenge solver.
"""
import argparse
import datetime as dt
import hashlib
import io
import json
import re
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

from pypdf import PdfReader

BASE = "https://dis.puc.state.oh.us/"
RESTRICTED = re.compile(r"^Confidential Document Target:", re.I)


def public_pdf(data: bytes, expected_pages: int) -> list[str]:
    if not data.startswith(b"%PDF-"):
        raise ValueError("DIS returned a non-PDF response; access remains unverified")
    reader = PdfReader(io.BytesIO(data))
    if len(reader.pages) != expected_pages:
        raise ValueError("PDF page count differs from the public record")
    return [page.extract_text() or "" for page in reader.pages]


def verify_filing(texts: list[str], case_no: str, filed: str):
    """Check the agency's appended filing receipt, not just a PDF filename."""
    receipt = texts[-1]
    month, day, year = map(int, filed.split("/"))
    dates = re.findall(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", receipt)
    if (month, day, year) not in [tuple(map(int, d)) for d in dates]:
        raise ValueError("Downloaded filing receipt date does not match docket")
    case_year, case_number = map(int, case_no.split("-"))
    cases = re.findall(r"\b(\d{2})-(\d+)-EL-BLN\b", receipt, re.I)
    if (case_year, case_number) not in [tuple(map(int, c)) for c in cases]:
        raise ValueError("Downloaded filing receipt case does not match docket")


def archive_verified(record: dict, data: bytes, texts: list[str]):
    """Preserve only verified bytes in the existing private source archive."""
    from btw_engine.fetch import BUCKET, s3, upsert_document
    client = s3()
    key = f"docs/{record['sha256']}.pdf"
    if not client.list_objects_v2(Bucket=BUCKET, Prefix=key).get("KeyCount", 0):
        client.put_object(Bucket=BUCKET, Key=key, Body=data,
                          ContentType="application/pdf",
                          Metadata={"source-url": record["pdf_url"]})
    upsert_document({"source_id": "opsb-dockets", "url": record["pdf_url"],
                     "r2_key": key, "sha256": record["sha256"],
                     "doc_genre": "opsb_filing", "pages": record["page_count"]})
    text_key = f"reviews/opsb-2026-10-28/{record['sha256']}.json"
    if not client.list_objects_v2(Bucket=BUCKET, Prefix=text_key).get("KeyCount", 0):
        client.put_object(Bucket=BUCKET, Key=text_key,
                          Body=json.dumps({**record, "pages": texts},
                                          ensure_ascii=False).encode(),
                          ContentType="application/json")


def capture(manifest: Path, *, limit: int | None = None,
            output: Path | None = None, archive: bool = False):
    from playwright.sync_api import sync_playwright
    records = json.loads(manifest.read_text())
    ids = [r["external_id"] for r in records]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r"[0-9a-f-]{36}", x) for x in ids):
        raise ValueError("Manifest must contain unique docket UUIDs")
    if limit is not None and limit < 1:
        raise ValueError("Limit must be positive")
    selected = records[:limit]
    cases = sorted({r["payload"]["case_no"] for r in selected})
    if any(not re.fullmatch(r"\d{2}-\d{4}", c) for c in cases):
        raise ValueError("Invalid manifest case")
    if output:
        output.mkdir(parents=True, exist_ok=True)
    results = []
    failures = 0
    with tempfile.TemporaryDirectory(prefix="btw-opsb-public-") as profile, sync_playwright() as runtime:
        default = Path(profile) / "Default"
        default.mkdir()
        (default / "Preferences").write_text(json.dumps({
            "plugins": {"always_open_pdf_externally": True}}))
        context = runtime.chromium.launch_persistent_context(
            profile, headless=False, accept_downloads=True)
        try:
            page = context.new_page()
            page.set_default_timeout(60_000)
            page.goto(BASE, wait_until="domcontentloaded")
            try:
                page.locator("#txtCaseLookup").wait_for(state="visible", timeout=30_000)
            except Exception:
                # One ordinary refresh for an intermittent rejected home response.
                # No challenge interaction or alternative transport.
                print("Public home unavailable; one bounded ordinary refresh", flush=True)
                page.wait_for_timeout(10_000)
                page.reload(wait_until="domcontentloaded")
                page.locator("#txtCaseLookup").wait_for(state="visible", timeout=30_000)
            for case_no in cases:
                page.locator("#txtCaseLookup").fill(case_no)
                page.locator('#caselookupform input[type="image"]').click()
                grid = page.locator("#ContentPlaceHolderMaster_gvDocketInformation")
                grid.wait_for(state="visible")
                grid_url = page.url
                print(f"CASE {case_no}: ordinary paginated docket", flush=True)
                for record in (r for r in selected if r["payload"]["case_no"] == case_no):
                    doc_id = record["external_id"]
                    if RESTRICTED.search(record["payload"]["summary"]):
                        results.append({**record, "status": "confidential_metadata_only"})
                        print(f"RESTRICTED {doc_id}: metadata only", flush=True)
                        continue
                    try:
                        for _ in range(4):
                            grid.wait_for(state="visible")
                            document_link = grid.locator(f'a[href*="{doc_id}"]')
                            if document_link.count():
                                break
                            page.locator("#ContentPlaceHolderMaster_NextPage").click()
                        else:
                            raise ValueError("Document missing from bounded public docket pages")
                        with context.expect_page() as opened:
                            document_link.click(button="middle")
                        document_page = opened.value
                        link = document_page.get_by_role("link", name="View Document", exact=True).first
                        link.wait_for(state="visible")
                        text = document_page.locator("body").inner_text()
                        filed = record["payload"]["filed"]
                        m, d, y = map(int, filed.split("/"))
                        if f"{m}/{d}/{y}" not in text or case_no not in text:
                            raise ValueError("Docket date/case identity mismatch")
                        count = re.search(r"Number of pages:\s*(\d+)", text)
                        if not count:
                            raise ValueError("Missing official page count")
                        url = urljoin(document_page.url, link.get_attribute("href"))
                        if urlparse(url).netloc != "dis.puc.state.oh.us" or urlparse(url).path != "/ViewImage.aspx":
                            raise ValueError("Document link is not an official DIS PDF")
                        local = output / f"{doc_id}.pdf" if output else None
                        if local and local.exists():
                            data = local.read_bytes()
                        else:
                            # The HTML/302 responses before this download are expected.
                            # No archive write occurs until the actual file is verified.
                            with document_page.expect_download(timeout=60_000) as pending:
                                link.click(no_wait_after=True)
                            data = Path(pending.value.path()).read_bytes()
                        texts = public_pdf(data, int(count[1]))
                        verify_filing(texts, case_no, filed)
                        current = {**record, "pdf_url": url, "record_text": text,
                                   "sha256": hashlib.sha256(data).hexdigest(),
                                   "page_count": len(texts), "status": "verified_public_pdf",
                                   "verified_at": dt.datetime.now(dt.timezone.utc).isoformat()}
                        if output:
                            local.write_bytes(data)
                            (output / f"{doc_id}.json").write_text(json.dumps(
                                {**current, "pages": texts}, ensure_ascii=False))
                        if archive:
                            archive_verified(current, data, texts)
                        results.append(current)
                        print(f"VERIFIED {doc_id}: {len(texts)} pages; sha256 {current['sha256']}", flush=True)
                        document_page.close()
                    except Exception as exc:
                        failures += 1
                        print(f"STATE {page.url}: {page.locator('body').inner_text()[:400]}", flush=True)
                        if output:
                            page.screenshot(path=str(output / "last-failure.png"))
                        results.append({**record, "status": "unavailable", "error": str(exc)})
                        print(f"UNAVAILABLE {doc_id}: {exc}", flush=True)
                        if failures >= 2:
                            raise RuntimeError("Two consecutive access failures; stop this session") from exc
                    if 'document_page' in locals() and not document_page.is_closed():
                        document_page.close()
                    if output:
                        (output / "index.json").write_text(json.dumps(results, ensure_ascii=False))
        finally:
            context.close()
    if failures:
        raise RuntimeError(f"{failures}/{len(results)} document files unavailable; no fact promotion")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--output", type=Path)
    ap.add_argument("--archive", action="store_true")
    args = ap.parse_args()
    capture(args.manifest, limit=args.limit, output=args.output, archive=args.archive)


if __name__ == "__main__":
    main()
