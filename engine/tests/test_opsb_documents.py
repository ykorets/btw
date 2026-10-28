import io

import pytest
from pypdf import PdfWriter

from btw_engine.opsb_documents import public_pdf


def blank_pdf(pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(100, 100)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def test_rejects_html_challenge_even_on_http_200():
    with pytest.raises(ValueError, match="non-PDF"):
        public_pdf(b"<html>Request Rejected</html>", 2)


def test_rejects_partial_or_wrong_document():
    with pytest.raises(ValueError, match="page count"):
        public_pdf(blank_pdf(1), 2)


def test_accepts_complete_public_pdf():
    assert public_pdf(blank_pdf(2), 2) == ["", ""]


from btw_engine.opsb_documents import verify_filing, RESTRICTED


def test_filing_receipt_rejects_a_different_same_length_document():
    with pytest.raises(ValueError, match="case"):
        verify_filing(["10/26/2026 Case No(s). 26-0169-EL-BLN"], "25-0185", "10/26/2026")
    with pytest.raises(ValueError, match="date"):
        verify_filing(["10/25/2026 Case No(s). 25-0185-EL-BLN"], "25-0185", "10/26/2026")


def test_public_receipt_supports_unpadded_case_and_date():
    verify_filing(["3/7/2025 Case No(s). 25-185-EL-BLN"], "25-0185", "03/07/2025")


def test_restricted_targets_are_distinct_from_public_protective_motions():
    assert RESTRICTED.search("Confidential Document Target: Confidential Letter")
    assert not RESTRICTED.search("Public motion concerning confidential information")


def test_public_browser_does_not_inherit_privileged_writer_credentials():
    from btw_engine.opsb_documents import browser_environment
    assert browser_environment({"PATH": "/bin", "DISPLAY": ":99",
        "R2_SECRET": "test", "R2_ACCESS_KEY": "test",
        "SUPABASE_SERVICE_KEY": "test", "ACTIONS_RUNTIME_TOKEN": "test",
        "GITHUB_TOKEN": "test"}) == {"PATH": "/bin", "DISPLAY": ":99"}
