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
