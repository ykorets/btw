"""M5 — deterministic planner tests for normalize."""

from btw_engine.normalize import plan_unit_changes, resolve_facility

TITAN = {"id": "u-titan", "facility_id": "f1", "oem": "Solar Turbines",
         "model": "Titan 350", "unit_count": 6, "mw_each": 31.7,
         "fuel": "natural_gas", "hours_permitted": 8760}
LM = {"id": "u-lm", "facility_id": "f1", "oem": "GE Vernova",
      "model": "LM2500", "unit_count": 5, "mw_each": 34.1,
      "fuel": "natural_gas", "hours_permitted": 8760}


def _c(cid, field, value, value_num, quote):
    return {"id": cid, "field": field, "value": value, "value_num": value_num,
            "quote": quote, "match_score": 1.0}


def test_quote_token_binding_stages_updates():
    claims = [
        _c("c1", "unit.count", "five", 5,
           "Reduce the count of Titan 350 simple-cycle turbines from six to "
           "five and increase the site-rated power to 38 MW per turbine"),
        _c("c2", "unit.mw_each", "38 MW", 38.0,
           "increase the site-rated power to 38 MW per turbine for the "
           "Titan 350 units"),
        _c("c3", "unit.mw_each", "34.1 MW", 34.1,
           "reduce the site-rated power to 34.1 MW per turbine for the "
           "GE LM2500"),
    ]
    links, updates, conflicts = plan_unit_changes([TITAN, LM], claims)
    assert not conflicts
    # Titan gets two staged changes (count 6->5, mw 31.7->38)
    assert set(updates["u-titan"]) == {"unit_count", "mw_each"}
    assert updates["u-titan"]["mw_each"][0] == 38.0
    # LM2500's 34.1 equals published -> corroboration link, not an update
    assert "u-lm" not in updates
    assert ("u-lm" in {u["id"] for u, f, c in links if f == "mw_each"})


def test_no_model_token_in_quote_means_no_binding():
    claims = [_c("c4", "unit.hours_permitted", "8,760 hours", 8760,
                 "increase the annual hours of operation from 5,880 to "
                 "8,760 hours per turbine.")]
    links, updates, conflicts = plan_unit_changes([TITAN, LM], claims)
    assert not links and not updates and not conflicts


def test_conflicting_values_are_skipped():
    claims = [
        _c("c5", "unit.mw_each", "38", 38.0, "Titan 350 rated 38 MW"),
        _c("c6", "unit.mw_each", "39", 39.0, "Titan 350 rated 39 MW"),
    ]
    _links, updates, conflicts = plan_unit_changes([TITAN], claims)
    assert conflicts
    assert "mw_each" not in updates.get("u-titan", {})


def test_model_claim_links():
    claims = [_c("c7", "unit.model", "350", None, "Titan 350")]
    links, _u, _x = plan_unit_changes([TITAN, LM], claims)
    assert [(u["id"], f) for u, f, c in links] == [("u-titan", "model")]


def test_resolve_facility_unique_permit():
    permits = [{"id": "p1", "permit_no": "177263", "facility_id": "f1"},
               {"id": "p2", "permit_no": "01156-01PC", "facility_id": "f2"}]
    claims = [_c("c8", "permit.no", "177263", 177263, "Permit No. 177263")]
    assert resolve_facility(claims, permits) == "f1"
    # ambiguous or missing -> None
    assert resolve_facility([], permits) is None
    both = [_c("c9", "permit.no", "177263", None, "q"),
            _c("c10", "permit.no", "01156-01PC", None, "q")]
    assert resolve_facility(both, permits) is None
