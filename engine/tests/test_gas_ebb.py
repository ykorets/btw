"""IW-v1 — gas EBB parser tests (pure functions, fixture from live probe)."""

from btw_engine.watch import gas_ebb_candidates, parse_gas_ebb_csv

# Trimmed from the live 2026-07-13 TD2 CSV (base64-decoded), quirks kept:
# BOM, quoted comma in TSP name, double space in the OA column header.
CSV = (
    "﻿TSP Name,TSP,Post Date/Time,Effective Gas Day,Effective Time,"
    "LineCode,Segment,Loc,Loc Name,Loc Zn,Loc Purp Desc,Loc/QTI,Flow Ind,"
    "Design Capacity,Operating Capacity,Total Scheduled Quantity,"
    "Operationally  Available Capacity,IT,All Qty Avail,"
    "Quantity Not Available Reason,Meas Basis Desc\r\n"
    '"Texas Gas Transmission, LLC",115972101,20260713 11:07:00,20260713,'
    "12:00:00,200,,,East,,Pipeline Segment defined by 1 location,SGQ,BD,"
    "34420,34420,5369,29051,,Y,,MMBtu\r\n"
    '"Texas Gas Transmission, LLC",115972101,20260713 11:07:00,20260713,'
    "12:00:00,925,LKC,26096,MZX Southaven,1,Delivery Location,DPQ,D,,,"
    "177000,,N,N,Design and/or Operating Capacity has not been specified "
    "by the TSP,MMBtu\r\n"
)


def test_parser_finds_watched_location_only():
    rows = parse_gas_ebb_csv(CSV, ["MZX"])
    assert len(rows) == 1
    assert rows[0]["Loc"] == "26096"
    assert rows[0]["Loc Name"] == "MZX Southaven"
    assert rows[0]["Total Scheduled Quantity"] == "177000"


def test_parser_needle_is_case_insensitive():
    assert parse_gas_ebb_csv(CSV, ["mzx"])
    assert not parse_gas_ebb_csv(CSV, ["NOPE"])


def test_candidates_one_per_location_and_gas_day():
    rows = parse_gas_ebb_csv(CSV, ["MZX"])
    cands = gas_ebb_candidates(rows, "TD2", "https://x/doc?id=1")
    assert len(cands) == 1
    c = cands[0]
    assert c["external_id"] == "tgt-26096-20260713"  # cycle NOT in the id
    assert "177000 MMBtu" in c["title"]
    assert c["payload"]["scheduled_mmbtu"] == 177000.0
    assert c["payload"]["kw_hit"] is False  # flowing = normal, stays quiet


def test_zero_flow_is_the_signal():
    rows = parse_gas_ebb_csv(CSV.replace(",177000,", ",,"), ["MZX"])
    cands = gas_ebb_candidates(rows, "TD2", "u")
    assert cands[0]["payload"]["scheduled_mmbtu"] == 0.0
    assert cands[0]["payload"]["kw_hit"] is True  # silence surfaces in review
