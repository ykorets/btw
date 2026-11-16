"""New facilities from TCEQ filings: tests on real agency wording."""

import os
from pathlib import Path

os.environ.setdefault("BTW_API_URL", "https://example.test")
os.environ.setdefault("BTW_API_KEY", "test-key")

from btw_engine import bootstrap, intake  # noqa: E402
from btw_engine.extract import deterministic_claims  # noqa: E402
from btw_engine.tceq_records import parse_results  # noqa: E402

# Nexus Hubbard Power, TCEQ permit 183046 draft special conditions and PDS
# header (Records Online, 2026-09-18), as pypdf extracts them.
CONDITIONS = (
    "Permit Numbers 183046, PSDTX1700, and PSDTXGHG269Texas Commission on "
    "Environmental Quality rating SpecificationsThis permit authorizes the "
    "following:5.Four (4) natural gas fired CTGs to operate in combined cycle "
    "mode (EPNs CT-B1, CT-B2, A.CT-B3, and CT-B4).  All turbines are GE Frame "
    "7F.04, each with an annual average heat input of  2,031.3million British "
    "thermal units per hour (MMBtu/hr) and each with a rated gross nominal "
    "capacity of 353.2 megawatts (MW). Seven (7) natural gas fired CTGs to "
    "operate in combined cycle mode (EPNs CT-C1, CT-C2, B.CT-C3, CT-C4, CT-C5, "
    "CT-C6, and CT-C7).  All turbines are GE Frame 7HA.01, each with an annual "
    "average heat input of 2,779.3 million British thermal units per hour "
    "(MMBtu/hr) and each with a rated gross nominal capacity of 493.9 "
    "megawatts (MW). Eight (8) natural gas fired CTGs to operate in simple "
    "cycle mode (EPNs CT-D1, CT-D2, CT-C.D3, CT-D4, CT-D5, CT-D6, CT-D7, and "
    "CT-D8).  All turbines are GE LM6000 PC, each with an annual average heat "
    "input of 520.1 million British thermal units per hour (MMBtu/hr) and each "
    "with a rated gross nominal capacity of 63.7 megawatts (MW).")
PDS = ("TCEQ Interoffice Memorandum\nPreliminary Determination Summary\n"
       "Nexus Hubbard Power, LLC\nPermit Numbers 183046, PSDTX1700, and "
       "GHGPSDTX269 \nApplicantI.")


def _claims(text: str, doc_id: str, genre: str) -> list[dict]:
    out = []
    for i, claim in enumerate(deterministic_claims([text])):
        out.append({**claim, "id": f"{doc_id}-{i}", "document_id": doc_id,
                    "match_score": 1.0, "status": "validated",
                    "extractor_version": "deterministic-regulatory-v1",
                    "document": {"doc_genre": genre}})
    return out


def test_tceq_conditions_yield_turbine_cohorts_and_clean_permit_numbers():
    claims = deterministic_claims([CONDITIONS])
    numbers = sorted(c["value"] for c in claims if c["field"] == "permit.no")
    assert numbers == ["183046", "PSDTX1700", "PSDTXGHG269"]
    cohorts = {(c["entity_hint"], c["field"]): c["value"] for c in claims
               if c["field"].startswith("unit.")}
    assert cohorts[("GE 7F.04", "unit.count")] == "4"
    assert cohorts[("GE 7F.04", "unit.mw_each")] == "353.2"
    assert cohorts[("GE 7HA.01", "unit.count")] == "7"
    assert cohorts[("GE LM6000 PC", "unit.mw_each")] == "63.7"


def test_plan_facility_from_filing_documents():
    claims = (_claims(CONDITIONS, "doc-c", "tceq_special_conditions")
              + _claims(PDS, "doc-p", "tceq_preliminary_determination"))
    filing = {"record_no": "183046", "name": "NEXUS DATA CENTER HUBBARD",
              "county": "HILL"}
    scope = bootstrap.scoped_claims("183046", {
        "doc-c": [c for c in claims if c["document_id"] == "doc-c"],
        "doc-p": [c for c in claims if c["document_id"] == "doc-p"],
        "other": [{"field": "permit.no", "value": "999999",
                   "document_id": "other"}]})
    assert {c["document_id"] for c in scope} == {"doc-c", "doc-p"}

    plan = bootstrap.plan_facility(filing, scope)
    assert plan["skip"] is False
    assert plan["facility"] == {
        "slug": "nexus-data-center-hubbard-hill-tx",
        "name": "Nexus Data Center Hubbard", "state": "TX",
        "county": "Hill", "status": "filed"}
    units = sorted((u["row"]["model"], u["row"]["oem"], u["row"]["unit_count"],
                    u["row"].get("mw_each"), u["plan"]["basis"])
                   for u in plan["units"])
    assert units == [
        ("7F.04", "GE", 4, 353.2, "permitted"),
        ("7HA.01", "GE", 7, 493.9, "permitted"),
        ("LM6000 PC", "GE", 8, 63.7, "permitted"),
    ]
    permit = plan["permit"]
    assert permit["row"]["permit_no"] == "183046"
    assert permit["row"]["authority"] == "TCEQ"
    assert permit["row"]["status"] == "preliminary determination"
    assert permit["row"]["verification_state"] == "corroborated"


def test_competing_counts_skip_the_cohort_instead_of_guessing():
    claims = _claims(CONDITIONS, "doc-c", "tceq_special_conditions")
    claims.append({**next(c for c in claims if c["field"] == "unit.count"
                          and c["entity_hint"] == "GE 7F.04"),
                   "id": "doc-x-1", "document_id": "doc-x",
                   "value": "6", "value_num": 6})
    units, notes = bootstrap.plan_units(claims)
    assert sorted(u["row"]["model"] for u in units) == ["7HA.01", "LM6000 PC"]
    assert any("7F.04" in note for note in notes)


def test_no_permit_row_without_a_status_claim():
    claims = _claims(CONDITIONS, "doc-c", "tceq_special_conditions")
    assert bootstrap.plan_permit("183046", claims) is None


def test_records_online_results_parse_titles_genres_and_file_links():
    page = (Path(__file__).parent / "fixtures/tceq-records-183046.html").read_text()
    docs = parse_results(page)
    assert [d["title"] for d in docs] == ["Conditions", "MAERT", "Modeling",
                                          "Modeling"]
    assert docs[0]["genre"] == "tceq_special_conditions"
    assert docs[2]["genre"] is None
    assert docs[0]["primary_id"] == "183046"
    assert docs[0]["date"] == "2026-09-18"
    assert docs[0]["url"].endswith(
        "IdcService=TCEQ_APD_SEARCH_GET_FILE&xAPDParent=8647494&searchType=External")


def test_intake_downloads_new_useful_documents_best_evidence_first():
    filing = {"record_no": "183046"}
    found = [
        (filing, {"did": "1", "genre": "tceq_maert", "url": "u1"}),
        (filing, {"did": "2", "genre": None, "url": "u2"}),
        (filing, {"did": "3", "genre": "tceq_special_conditions", "url": "u3"}),
        (filing, {"did": "3", "genre": "tceq_special_conditions", "url": "u3"}),
        (filing, {"did": "4", "genre": "tceq_nsr_application", "url": "known"}),
    ]
    todo = intake.plan_downloads(found, {"known"}, limit=5)
    assert [doc["did"] for _f, doc in todo] == ["3", "1"]


def test_tceq_filings_put_nsr_numbers_before_psd_numbers():
    rows = [
        {"source_id": "tceq-permit-portal", "record_no": "PSDTX1700",
         "name": "NEXUS DATA CENTER HUBBARD", "first_seen": "2026-10-01"},
        {"source_id": "tceq-permit-portal", "record_no": "183046",
         "name": "NEXUS DATA CENTER HUBBARD", "first_seen": "2026-10-02"},
        {"source_id": "tceq-permit-portal", "record_no": "96940",
         "name": "DEER PARK ENERGY CENTER", "registry_status": "ISSUED PERMIT",
         "first_seen": "2026-10-02"},
        {"source_id": "tceq-permit-portal", "record_no": "185939",
         "name": "FREESTONE ENERGY CENTER",
         "registry_status": "NEW APPLICATION", "first_seen": "2026-11-14"},
        {"source_id": "opsb-dockets", "record_no": None, "first_seen": "x"},
    ]
    assert [f["record_no"] for f in bootstrap.tceq_filings(rows)] == [
        "183046", "185939", "PSDTX1700"]


def test_engine_cohort_wordings_from_standard_permit_reviews():
    woodrow = ("Twenty-five (25) natural gas fired Bergen B36:45L9AG model "
               "engines with approximately 5.18 megawatt (MW) capacity each.  "
               "Nineteen (19) natural gas fired Bergen B36:45V20 model engines "
               "with approximately 12.63 MW capacity each.")
    royal = ("Four 224 MW General Electric (GE) model 7F.05 combustion "
             "turbine generators (CTs) will combust clean")
    got = {(c["entity_hint"], c["field"]): c["value"]
           for c in deterministic_claims([woodrow + " " + royal])
           if c["field"].startswith("unit.")}
    assert got[("Bergen B36:45L9AG", "unit.count")] == "25"
    assert got[("Bergen B36:45V20", "unit.mw_each")] == "12.63"
    assert got[("GE 7F.05", "unit.count")] == "4"
    assert got[("GE 7F.05", "unit.mw_each")] == "224"


def test_planned_rows_and_receipts_pass_the_promotion_truth_gate():
    from btw_engine.truth import provenance_violations

    claims = (_claims(CONDITIONS, "doc-c", "tceq_special_conditions")
              + _claims(PDS, "doc-p", "tceq_preliminary_determination"))
    for claim in claims:
        claim["anchor"] = "quote"
        claim["numeric_check"] = True if claim.get("value_num") is not None else None
        claim["document"] = {**claim["document"], "r2_key": f"docs/{claim['document_id']}.pdf"}
    plan = bootstrap.plan_facility(
        {"record_no": "183046", "name": "NEXUS DATA CENTER HUBBARD",
         "county": "HILL"}, claims)
    units, provenance = [], []
    for i, unit in enumerate(plan["units"]):
        units.append({**bootstrap.unit_row(unit), "id": f"u{i}"})
        for field, claim, _note, support, derivation in bootstrap.unit_receipts(unit):
            provenance.append({"fact_table": "unit", "fact_id": f"u{i}",
                               "fact_field": field, "claim": claim,
                               "support_kind": support, "derivation": derivation})
    permit = {**plan["permit"]["row"], "id": "p0"}
    for field, claim, _note, support, derivation in bootstrap.permit_receipts(plan["permit"]):
        provenance.append({"fact_table": "permit", "fact_id": "p0",
                           "fact_field": field, "claim": claim,
                           "support_kind": support, "derivation": derivation})
    facility = {"slug": plan["facility"]["slug"], "unit": units,
                "permit": [permit]}
    assert provenance_violations([facility], provenance) == []
