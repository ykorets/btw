"""Bounded public OPSB document capture, using an ordinary DIS session.

PDF identity and page count must agree with the official record before any
local file write. Confidential docket entries are metadata-only. No challenge
solver, custom browser fingerprint, proxy or retained authentication state.
"""
import argparse
import hashlib
import io
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

from pypdf import PdfReader


BASE = "https://dis.puc.state.oh.us/"


def public_pdf(data: bytes, expected_pages: int) -> list[str]:
    if not data.startswith(b"%PDF-"):
        raise ValueError("DIS returned a non-PDF response; access remains unverified")
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted or len(reader.pages) != expected_pages:
        raise ValueError("PDF encryption/page count differs from the public record")
    return [page.extract_text() or "" for page in reader.pages]


def capture(manifest: Path, *, limit: int | None = None,
            output: Path | None = None):
    from playwright.sync_api import sync_playwright
    records = json.loads(manifest.read_text())
    ids = [r["external_id"] for r in records]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r"[0-9a-f-]{36}", x) for x in ids):
        raise ValueError("Manifest must contain unique docket UUIDs")
    if output:
        output.mkdir(parents=True, exist_ok=True)
    results = []
    failures = 0
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=False)
        try:
            page = browser.new_page()
            page.set_default_timeout(60_000)
            page.goto(BASE, wait_until="domcontentloaded")
            page.locator("#txtCaseLookup").wait_for(state="visible")
            for record in records[:limit]:
                doc_id = record["external_id"]
                # The official docket description explicitly identifies restricted entries.
                if re.search(r"confidential", record["payload"]["summary"], re.I):
                    results.append({**record, "status": "confidential_metadata_only"})
                    print(f"RESTRICTED {doc_id}: metadata only", flush=True)
                    continue
                try:
                    page.goto(BASE + "DocumentRecord.aspx?DocID=" + doc_id,
                              wait_until="domcontentloaded")
                    link = page.get_by_role("link", name="View Document", exact=True).first
                    link.wait_for(state="visible")
                    text = page.locator("body").inner_text()
                    filed = record["payload"]["filed"]
                    # DIS uses unpadded months/days in its document metadata.
                    m, d, y = map(int, filed.split("/"))
                    if f"{m}/{d}/{y}" not in text or record["payload"]["case_no"] not in text:
                        raise ValueError("Docket date/case identity mismatch")
                    count = re.search(r"Number of pages:\s*(\d+)", text)
                    if not count:
                        raise ValueError("Missing official page count")
                    url = urljoin(page.url, link.get_attribute("href"))
                    if urlparse(url).netloc != "dis.puc.state.oh.us" or urlparse(url).path != "/ViewImage.aspx":
                        raise ValueError("Document link is not an official DIS PDF")
                    # Follow the public control with Chromium's ordinary navigation
                    # rather than an independent HTTP client with different transport.
                    with page.expect_response(url, timeout=60_000) as pending:
                        link.click()
                    response = pending.value
                    if not response.ok:
                        raise ValueError(f"Document HTTP {response.status}")
                    data = response.body()
                    texts = public_pdf(data, int(count[1]))
                    current = {**record, "pdf_url": url, "record_text": text}
                    result = {**current, "status": "verified_public_pdf",
                              "sha256": hashlib.sha256(data).hexdigest(),
                              "page_count": len(texts)}
                    if output:
                        output.mkdir(parents=True, exist_ok=True)
                        (output / f"{doc_id}.pdf").write_bytes(data)
                        (output / f"{doc_id}.json").write_text(json.dumps(
                            {**current, "pages": texts}, ensure_ascii=False))
                    results.append(result)
                    print(f"VERIFIED {doc_id}: {len(texts)} pages; sha256 {result['sha256']}", flush=True)
                except Exception as exc:
                    failures += 1
                    results.append({**record, "status": "unavailable", "error": str(exc)})
                    print(f"UNAVAILABLE {doc_id}: {exc}", flush=True)
        finally:
            browser.close()
    if output:
        (output / "index.json").write_text(json.dumps(results, ensure_ascii=False))
    if failures:
        raise RuntimeError(f"{failures}/{len(results)} document files unavailable; no fact promotion")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    capture(args.manifest, limit=args.limit, output=args.output)


if __name__ == "__main__":
    main()
