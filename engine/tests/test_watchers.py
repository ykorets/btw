"""M4 fixture replay — a parser that stops understanding its fixture fails CI
before it fails in production (docs/plan.md M4 DoD)."""

import os

from btw_engine.watch import parse_tceq_ascii, parse_opsb_case_html

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _read(name: str) -> str:
    with open(os.path.join(FIX, name)) as fp:
        return fp.read()


def test_tceq_ascii_parses_all_rows():
    rows = parse_tceq_ascii(_read("tceq_nsr_pending.txt"))
    assert len(rows) == 5
    assert all(r["program"] == "NSR" for r in rows)


def test_tceq_fields():
    rows = parse_tceq_ascii(_read("tceq_nsr_pending.txt"))
    stella = next(r for r in rows if "STELLA" in r["customer_name"])
    assert stella["project_number"] == "411957"
    assert stella["permit_type"] == "STDPMT"
    assert stella["county"] == "CALDWELL"
    assert stella["rcv_date"] == "07/10/2026"
    assert stella["regulated_entity"] == "RN112486659"


def test_tceq_tolerates_short_rows():
    # last fixture row has an empty trailing Rules column
    rows = parse_tceq_ascii(_read("tceq_nsr_pending.txt"))
    van = next(r for r in rows if "VAN EATON" in r["customer_name"])
    assert van["rules"] == ""


def test_tceq_raw_http_format():
    # Raw wire format: fields on separate \r\n\t lines, rows split by <br />,
    # empty dates as &nbsp; chains. Regression for daily-intake run #2, where
    # line-based parsing produced one empty phantom row.
    rows = parse_tceq_ascii(_read("tceq_nsr_raw.html"))
    assert len(rows) == 2
    stella = next(r for r in rows if "STELLA" in r["customer_name"])
    assert stella["project_number"] == "411957"
    assert stella["county"] == "CALDWELL"
    assert stella["rcv_date"] == "07/10/2026"
    assert stella["complete_date"] == ""   # &nbsp; chain → empty
    lhoist = next(r for r in rows if "LHOIST" in r["customer_name"])
    assert lhoist["rules"].startswith("6001")


def test_opsb_case_parses_filings():
    rows = parse_opsb_case_html(_read("opsb_case_25-0185.html"), "25-0185")
    assert len(rows) == 3
    assert rows[0]["external_id"] == "398508a4-ef98-440f-8d48-c629d2937816"
    assert rows[0]["payload"]["filed"] == "04/29/2026"
    assert "Condition 12" in rows[0]["payload"]["summary"]
    assert rows[0]["url"].startswith(
        "https://dis.puc.state.oh.us/DocumentRecord.aspx?DocID=")


def test_opsb_titles_carry_case_number():
    rows = parse_opsb_case_html(_read("opsb_case_25-0185.html"), "25-0185")
    assert all(t["title"].startswith("25-0185 ") for t in rows)
