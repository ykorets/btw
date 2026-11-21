"""Regression cover for the 2026-07-21 silent-failure incident.

Three adapters (tceq-nsr-stdpmt, tceq-nsr-psd, gdelt-news) were down while the
morning report said 🟢: failures were printed to a runner log nobody reads, the
run exited 0, and the only alarm was an SLO check days away. These tests pin the
pieces that make a failure legible.
"""

import httpx

from btw_engine.watch import describe_error, is_gdelt_throttled, parse_tceq_ascii


def _resp(status: int, body: str, ct: str = "text/html") -> httpx.Response:
    return httpx.Response(status, text=body, headers={"content-type": ct},
                          request=httpx.Request("GET", "https://example.test/x"))


# ------------------------------------------------------------ describe_error --

def test_describe_error_keeps_status_and_body_for_http_errors():
    r = _resp(403, "<html><body>Request Rejected. Support ID: 42</body></html>")
    detail = describe_error(httpx.HTTPStatusError("boom", request=r.request,
                                                  response=r))
    assert "HTTP 403" in detail
    assert "Request Rejected" in detail      # WAF page is diagnosable
    assert "ct=text/html" in detail


def test_describe_error_collapses_whitespace_so_the_line_stays_readable():
    r = _resp(500, "line one\n\n\tline   two")
    detail = describe_error(httpx.HTTPStatusError("boom", request=r.request,
                                                  response=r))
    assert "line one line two" in detail


def test_describe_error_names_the_url_for_transport_failures():
    req = httpx.Request("GET", "https://tceq.example/search")
    detail = describe_error(httpx.ConnectTimeout("timed out", request=req))
    assert "ConnectTimeout" in detail
    assert "tceq.example" in detail


def test_describe_error_falls_back_to_type_and_message():
    detail = describe_error(RuntimeError("0 filings parsed"))
    assert detail == "RuntimeError: 0 filings parsed"


# --------------------------------------------------------- gdelt throttling --

def test_gdelt_throttle_detected_on_429():
    assert is_gdelt_throttled(_resp(429, "slow down"))


def test_gdelt_throttle_detected_on_200_with_plaintext_notice():
    """The failure that broke gdelt-news: rate limiting served as HTTP 200."""
    body = ("Please limit requests to one every 5 seconds or contact "
            "kalev.leetaru5@gmail.com for larger queries.")
    assert is_gdelt_throttled(_resp(200, body, ct="text/plain"))


def test_gdelt_success_is_not_mistaken_for_throttling():
    ok = _resp(200, '{"articles": []}', ct="application/json")
    assert not is_gdelt_throttled(ok)


# -------------------------------------------------------------- tceq parser --

def test_tceq_parser_unescapes_entities_in_company_names():
    """&amp; used to leak verbatim into candidate titles and payloads."""
    row = ("NSR|80197|STDPMT|EFFECTIVE|412274|EAGLE READY MIX|"
           "Eagle Ready Mix &amp; Concrete LLC|CN603943606|NEW|07/20/2026|"
           "|08/19/2026|PENDING|STANDARD PERMIT|RN112|Loc|R10|ANGELINA|"
           "LUFKIN|rules")
    parsed = parse_tceq_ascii(row)
    assert len(parsed) == 1
    assert parsed[0]["legal_name"] == "Eagle Ready Mix & Concrete LLC"


def test_tceq_parser_does_not_resurrect_tags_from_escaped_markup():
    """Unescaping happens after tag stripping, so &lt;b&gt; stays inert text."""
    row = ("NSR|80197|STDPMT|EFFECTIVE|412275|X|&lt;b&gt;Acme&lt;/b&gt;|CN1|NEW|"
           "07/20/2026||08/19/2026|PENDING|P|RN1|Loc|R10|COUNTY|CITY|rules")
    parsed = parse_tceq_ascii(row)
    assert parsed[0]["legal_name"] == "<b>Acme</b>"
