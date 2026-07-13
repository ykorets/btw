"""M6 stubs closed — FERC eLibrary + MDEQ notice parser tests.

Fixtures trimmed from live probes (2026-07-13)."""

from btw_engine.watch import ferc_candidates, mdeq_candidates, parse_mdeq_notice

FERC_PAYLOAD = {
    "totalHits": 12,
    "success": True,
    "searchHits": [
        {"documentId": "7EBE3C14-0BC3-C1F2-8550-9F4878400000",
         "description": "(doc-less) Motion to Intervene of Data Center "
                        "Coalition under EL26-72.",
         "category": "Submittal", "acesssionNumber": "20260709-5268",
         "filedDate": "07/09/2026",
         "docketNumbers": ["EL26-72-000"]},
        {"documentId": "AAAA", "description": "Comments of Example Co on "
                        "colocated load interconnection framework",
         "category": "Submittal", "acesssionNumber": "20260710-0001",
         "filedDate": "07/10/2026", "docketNumbers": ["AD25-7-000"]},
    ],
}


def test_ferc_candidates_map_accession_and_docket():
    out = ferc_candidates(FERC_PAYLOAD, ["colocat"])
    assert len(out) == 2
    assert out[0]["external_id"] == "ferc-20260709-5268"
    assert "EL26-72-000" in out[0]["title"]
    assert out[0]["payload"]["kw_hit"] is False
    assert out[1]["payload"]["kw_hit"] is True  # "colocated" matched


MDEQ_HTML = """
<table id="uxGrid"><tr><th><a href="#">Name</a></th><th>Permit Type</th>
<th>City</th><th>County</th><th>End Date</th><th>Notice</th><th>Draft</th>
<th>Other</th></tr>
<tr><td>Airgas Carbonic Inc, Star Plant</td>
<td>Water<br/>NPDES Minor Industrial, NPDES Commercial Renewal</td>
<td>Star</td><td>Rankin</td><td>07/17/2026</td><td></td><td></td><td></td></tr>
<tr><td>Entergy Mississippi LLC, Traceview Advanced Power Station</td>
<td>Air<br/>Air-Construction PSD</td>
<td>Ridgeland</td><td>Madison</td><td>07/14/2026</td>
<td></td><td></td><td></td></tr></table>
"""


def test_mdeq_parser_skips_header_and_reads_rows():
    rows = parse_mdeq_notice(MDEQ_HTML)
    assert len(rows) == 2
    assert rows[1]["name"].startswith("Entergy Mississippi")
    assert "Air-Construction PSD" in rows[1]["permit_type"]
    assert rows[1]["county"] == "Madison"


def test_mdeq_candidates_kw_and_idempotent_id():
    rows = parse_mdeq_notice(MDEQ_HTML)
    kws = ["power station", "data center"]
    a = mdeq_candidates(rows, kws, "https://x")
    b = mdeq_candidates(rows, kws, "https://x")
    assert [c["external_id"] for c in a] == [c["external_id"] for c in b]
    assert a[0]["payload"]["kw_hit"] is False   # Airgas
    assert a[1]["payload"]["kw_hit"] is True    # "Power Station"
    assert a[1]["title"].startswith("MDEQ draft permit: Entergy")
