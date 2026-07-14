"""publish: provenance folding + event source passthrough."""

import json
import os

os.environ.setdefault("SUPABASE_URL", "https://example.test")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")

from btw_engine import publish  # noqa: E402
from btw_engine.truth import provenance_violations  # noqa: E402


def _facility():
    return {
        "id": "fac-1", "slug": "crusoe-stargate-abilene", "name": "Crusoe",
        "state": "TX", "status": "operating", "flags": [],
        "unit": [{"id": "unit-1", "oem": "Solar", "model": "Titan 350",
                  "unit_count": 6, "mw_each": 38, "hours_permitted": 8760}],
        "permit": [{"id": "perm-1", "authority": "TCEQ",
                    "permit_no": "177263", "permit_type": "standard",
                    "status": "issued", "filed_at": None, "issued_at": None}],
    }


def test_attach_sources_folds_and_dedupes(monkeypatch):
    fac = _facility()
    url = "https://records.tceq.texas.gov/doc"
    sha = "13bd44e0d6070bd8d1c616d4832c162dd7ee13fbe2e509e32e853248b3957176"
    monkeypatch.setenv("BTW_ARCHIVE_BASE_URL", "https://evidence.behindthewatt.com")
    document = {"url": url, "doc_genre": "tceq_standard_permit_review",
                "sha256": sha, "r2_key": f"docs/{sha}.pdf",
                "fetched_at": "2026-07-13T00:00:00Z"}
    prov = [
        {"fact_table": "unit", "fact_id": "unit-1", "fact_field": "mw_each",
         "note": "staged", "claim": {"quote": "38 MW per turbine", "page": 3,
                                     "document": document}},
        {"fact_table": "unit", "fact_id": "unit-1", "fact_field": "unit_count",
         "note": "corroborated", "claim": {"quote": "six turbines", "page": 3,
                                           "document": document}},
        # different facility id -> ignored
        {"fact_table": "facility", "fact_id": "other", "fact_field": "status",
         "note": "", "claim": {"quote": None, "page": 1,
                               "document": {"url": "https://x", "doc_genre": "permit"}}},
        # no url -> ignored
        {"fact_table": "facility", "fact_id": "fac-1", "fact_field": "status",
         "note": "", "claim": None},
    ]
    publish.attach_sources([fac], prov)

    assert len(fac["sources"]) == 1
    src = fac["sources"][0]
    assert src["url"] == url
    assert sorted(src["facts"]) == ["unit.mw_each", "unit.unit_count"]
    assert src["quote"] == "38 MW per turbine"
    assert src["page"] == 3
    assert src["archive_url"] == f"https://evidence.behindthewatt.com/docs/{sha}.pdf"
    assert src["sha256"] == sha
    assert src["archive_status"] == "public"
    # internal ids stripped: mirror schema stays stable
    assert "id" not in fac
    assert "id" not in fac["unit"][0]
    assert "id" not in fac["permit"][0]


def test_events_json_carries_source_url(tmp_path):
    fac = _facility()
    publish.attach_sources([fac], [])
    events = [{"event_date": "2026-07-13", "event_type": "permit_filed",
               "headline": "MDEQ notices Traceview PSD",
               "source_url": "https://opcgis.deq.state.ms.us/notice",
               "facility": None},
              {"event_date": "2026-06-18", "event_type": "regulatory",
               "headline": "FERC order", "facility": None}]
    summary = {"as_of": "2026-07-13", "operating_gw": 0.23}
    publish.write_files(str(tmp_path), [fac], events, summary)

    data = json.loads((tmp_path / "events.json").read_text())
    assert data["events"][0]["source_url"] == "https://opcgis.deq.state.ms.us/notice"
    assert data["events"][1]["source_url"] is None
    facs = json.loads((tmp_path / "facilities.json").read_text())
    assert facs["facilities"][0]["slug"] == "crusoe-stargate-abilene"


def test_announcements_are_separate_and_explicitly_unverified(tmp_path):
    announcements = [
        {"name": "Project A", "state": "TX", "operating_status": "Proposed",
         "reported_capacity_mw": 1500, "source_as_of": "2026-04-30",
         "source_document": {"url": "https://example.test/inventory.xlsx",
                             "doc_genre": "third_party_inventory"}},
        {"name": "Project B", "state": "OH", "operating_status": "Under Construction",
         "reported_capacity_mw": 500, "source_as_of": "2026-04-30",
         "source_document": {"url": "https://example.test/inventory.xlsx",
                             "doc_genre": "third_party_inventory"}},
    ]
    publish.write_files(str(tmp_path), [], [], {"operating_gw": 0},
                        announcements=announcements)

    data = json.loads((tmp_path / "announcements.json").read_text())
    assert data["classification"] == "third_party_reported_not_btw_verified"
    assert data["summary"]["reported_gw"] == 2.0
    assert data["summary"]["projects"] == 2
    assert data["announcements"][0]["source"]["doc_genre"] == "third_party_inventory"
    assert "source_document" in announcements[0]


def test_announcements_only_writes_no_verified_fleet_files(tmp_path):
    announcements = [{
        "name": "Project A", "state": "TX",
        "operating_status": "Proposed", "reported_capacity_mw": 1500,
        "source_as_of": "2026-04-30",
        "source_document": {"url": "https://example.test/inventory.xlsx",
                            "doc_genre": "third_party_inventory"},
    }]

    publish.write_announcements(str(tmp_path), announcements)

    assert (tmp_path / "announcements.json").exists()
    assert not (tmp_path / "facilities.json").exists()
    assert not (tmp_path / "summary.json").exists()


def _receipt(table, fact_id, fact_field, claim_field, value,
             *, value_num=None, numeric=False, support_kind="direct",
             derivation=None):
    return {
        "fact_table": table, "fact_id": fact_id, "fact_field": fact_field,
        "support_kind": support_kind, "derivation": derivation,
        "claim": {
            "field": claim_field, "value": str(value),
            "value_num": value_num, "status": "validated",
            "anchor": "quote", "quote": f"Source says {value}",
            "match_score": 1.0, "numeric_check": numeric,
            "document": {"url": "https://example.test/source.pdf",
                         "r2_key": "docs/sha256.pdf", "doc_genre": "permit"},
        },
    }


def test_truth_gate_requires_field_compatible_claims():
    facility = _facility()
    facility["unit"][0] = {"id": "unit-1", "oem": None, "model": None,
                           "unit_count": 6, "mw_each": None,
                           "fuel": None, "hours_permitted": None}
    wrong = [_receipt("unit", "unit-1", "unit_count", "unit.mw_each",
                      6, value_num=6, numeric=True)]
    violations = provenance_violations([facility], wrong)
    assert any("unit_count" in item and "unit.count" in item
               for item in violations)


def test_truth_gate_accepts_exact_archived_numeric_claim():
    facility = _facility()
    facility["unit"][0] = {"id": "unit-1", "oem": None, "model": None,
                           "unit_count": 6, "mw_each": None,
                           "fuel": None, "hours_permitted": None}
    facility["permit"] = []
    receipts = [_receipt("unit", "unit-1", "unit_count", "unit.count",
                         6, value_num=6, numeric=True)]
    assert provenance_violations([facility], receipts) == []


def test_truth_gate_requires_each_compound_status_component():
    facility = _facility()
    facility["unit"] = []
    facility["permit"][0] = {
        "id": "perm-1", "authority": None, "permit_no": None,
        "permit_type": None, "status": "issued; under appeal",
        "filed_at": None, "issued_at": None,
    }
    receipts = [_receipt("permit", "perm-1", "status", "permit.status",
                         "issued")]
    violations = provenance_violations([facility], receipts)
    assert violations == [
        "permit crusoe-stargate-abilene/perm-1.status: unsupported "
        "component(s): under appeal"]


def test_observed_total_is_supported_without_manufactured_per_unit_rating():
    facility = _facility()
    facility["permit"] = []
    facility["unit"] = [{
        "id": "unit-1", "oem": None, "model": None, "unit_count": None,
        "mw_each": None, "total_mw": 495, "fuel": None,
        "hours_permitted": None, "basis": "observed",
        "verification_state": "source_asserted",
    }]
    receipts = [
        _receipt("unit", "unit-1", "total_mw", "observation.mw", 495,
                 value_num=495, numeric=True),
        _receipt("unit", "unit-1", "basis", "observation.mw", 495,
                 value_num=495, numeric=True, support_kind="derived",
                 derivation="observation.* classifies this cohort as observed"),
        _receipt("unit", "unit-1", "verification_state", "observation.mw",
                 495, value_num=495, numeric=True, support_kind="derived",
                 derivation="one archived source supports source_asserted"),
    ]
    assert provenance_violations([facility], receipts) == []
    assert publish.facility_mw(facility) == 495


def test_reported_basis_requires_quantitative_cohort_claim():
    facility = _facility()
    facility["permit"] = []
    facility["unit"] = [{
        "id": "unit-1", "oem": None, "model": "SMT-130",
        "unit_count": 15, "mw_each": None, "total_mw": None,
        "fuel": None, "hours_permitted": None, "basis": "reported",
        "verification_state": "source_asserted",
    }]
    receipts = [
        _receipt("unit", "unit-1", "model", "unit.model", "SMT-130"),
        _receipt("unit", "unit-1", "basis", "unit.model", "SMT-130",
                 support_kind="derived", derivation="reported cohort"),
        _receipt("unit", "unit-1", "verification_state", "unit.model",
                 "SMT-130", support_kind="derived",
                 derivation="one archived source"),
        _receipt("unit", "unit-1", "unit_count", "unit.count", 15,
                 value_num=15, numeric=True),
    ]

    violations = provenance_violations([facility], receipts)

    assert any("basis" in item for item in violations)
