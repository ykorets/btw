"""publish: provenance folding + event source passthrough."""

import json
import os

os.environ.setdefault("SUPABASE_URL", "https://example.test")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")

from btw_engine import publish  # noqa: E402


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


def test_attach_sources_folds_and_dedupes():
    fac = _facility()
    url = "https://records.tceq.texas.gov/doc"
    prov = [
        {"fact_table": "unit", "fact_id": "unit-1", "fact_field": "mw_each",
         "note": "staged", "claim": {"quote": "38 MW per turbine", "page": 3,
                                     "document": {"url": url, "doc_genre": "tceq_standard_permit_review"}}},
        {"fact_table": "unit", "fact_id": "unit-1", "fact_field": "unit_count",
         "note": "corroborated", "claim": {"quote": "six turbines", "page": 3,
                                           "document": {"url": url, "doc_genre": "tceq_standard_permit_review"}}},
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
