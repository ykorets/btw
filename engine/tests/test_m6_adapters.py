"""M6 — pure-function tests for the new adapters."""

from btw_engine.watch import (annotate_research_candidate,
                              connection_mode_signals,
                              normalize_page_text, echo_counter_candidate,
                              gdelt_candidates, courtlistener_candidates)


def test_pagehash_normalization_is_stable_across_noise():
    a = "<html><head><script>var t=Date.now()</script><style>.x{}</style>" \
        "</head><body><h1>Public  Notices</h1>\n<p>Draft permit " \
        "01156-03PC</p><!-- ts:123 --></body></html>"
    b = "<html><head><script>var t=Date.now()+999</script><style>.y{}" \
        "</style></head><body><h1>Public Notices</h1>  <p>Draft permit " \
        "01156-03PC</p><!-- ts:456 --></body></html>"
    assert normalize_page_text(a) == normalize_page_text(b)
    assert "Draft permit 01156-03PC" in normalize_page_text(a)


def test_pagehash_normalization_detects_real_change():
    base = "<body><p>Draft permit 01156-03PC</p></body>"
    changed = "<body><p>Draft permit 01156-03PC</p><p>NEW: hearing</p></body>"
    assert normalize_page_text(base) != normalize_page_text(changed)


ECHO_RESULTS = {"QueryRows": "2", "SVRows": "0", "CVRows": "0", "V3Rows": "0",
                "FEARows": "0", "InfFEARows": "1", "INSPRows": "1",
                "FCERows": "0", "TotalPenalties": "$0"}


def test_echo_counter_candidate_hash_changes_with_activity():
    q = {"name": "xai-colossus", "p_fn": "colossus", "p_st": "TN"}
    c1 = echo_counter_candidate("xai-colossus", ECHO_RESULTS, q)
    bumped = dict(ECHO_RESULTS, INSPRows="2")
    c2 = echo_counter_candidate("xai-colossus", bumped, q)
    assert c1["external_id"] != c2["external_id"]
    assert "inspections 1" in c1["title"]
    assert c1["payload"]["counters"]["InfFEARows"] == "1"


def test_gdelt_candidates_dedupe_key_is_url_hash():
    payload = {"articles": [
        {"url": "https://example.com/a", "title": "Data center turbines",
         "domain": "example.com", "seendate": "20260710T060000Z"},
        {"url": "", "title": "no url — skipped"},
    ]}
    cands = gdelt_candidates(payload)
    assert len(cands) == 1
    assert cands[0]["url"] == "https://example.com/a"
    assert len(cands[0]["external_id"]) == 16
    assert cands[0]["payload"]["kw_hit"] is True
    assert "btm_discovery" in cands[0]["payload"]["research_tags"]


def test_connection_mode_signals_cover_explicit_discovery_language():
    text = ("The private-wire microgrid will operate behind the meter under "
            "a non-export agreement and provide on-site generation.")
    assert connection_mode_signals(text) == [
        "behind_the_meter", "non_export", "private_wire",
        "on_site_generation", "microgrid",
    ]


def test_connection_mode_annotation_is_a_review_signal_not_a_fact():
    candidate = annotate_research_candidate({
        "title": "Co-located load interconnection filing",
        "payload": {"description": "export-limited service"},
    })
    payload = candidate["payload"]
    assert payload["connection_mode_signals"] == [
        "co_located_load", "export_limited",
    ]
    assert payload["research_tags"] == ["connection_mode"]
    assert payload["kw_hit"] is True
    assert "connection_mode" not in candidate  # no asserted facility field


def test_stac_scene_candidates_cloud_filter_and_mapping():
    from btw_engine.watch import stac_scene_candidates
    payload = {"features": [
        {"id": "S2B_15SYU_20260708_0_L2A",
         "properties": {"datetime": "2026-07-08T16:53:59Z",
                        "eo:cloud_cover": 31.2},
         "assets": {"thumbnail": {"href": "https://x/preview.jpg"}}},
        {"id": "S2A_15SYU_20260703_0_L2A",
         "properties": {"datetime": "2026-07-03T16:54:01Z",
                        "eo:cloud_cover": 92.0},
         "assets": {}},
    ]}
    cands = stac_scene_candidates("xai-colossus-2", payload, max_cloud=60)
    assert len(cands) == 1  # 92% cloud scene filtered out
    c = cands[0]
    assert c["external_id"] == "xai-colossus-2:S2B_15SYU_20260708_0_L2A"
    assert c["url"] == "https://x/preview.jpg"
    assert "2026-07-08" in c["title"] and "31.2" in c["title"]


def test_courtlistener_candidates():
    payload = {"results": [{
        "docket_id": 70000001, "caseName": "NAACP v. xAI Corp",
        "court": "W.D. Tenn.", "dateFiled": "2025-06-17",
        "docket_absolute_url": "/docket/70000001/naacp-v-xai/"}]}
    cands = courtlistener_candidates("xAI Corp", payload)
    assert cands[0]["external_id"] == "cl-70000001"
    assert cands[0]["url"].endswith("/docket/70000001/naacp-v-xai/")
    assert "xAI Corp:" in cands[0]["title"]
