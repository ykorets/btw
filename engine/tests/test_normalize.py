"""M5 — deterministic planner tests for normalize (+M3.5 context binding)."""

from btw_engine import normalize

from btw_engine.normalize import (
    _basis_claim,
    _unit_basis,
    _unit_receipt_compatible,
    copy_provenance,
    locate_context,
    plan_permit_links,
    plan_permit_changes,
    plan_unit_changes,
    resolve_facility,
    resolve_permit,
    staged_copy,
)

TITAN = {"id": "u-titan", "facility_id": "f1", "oem": "Solar Turbines",
         "model": "Titan 350", "unit_count": 6, "mw_each": 31.7,
         "fuel": "natural_gas", "hours_permitted": 8760}
LM = {"id": "u-lm", "facility_id": "f1", "oem": "GE Vernova",
      "model": "LM2500", "unit_count": 5, "mw_each": 34.1,
      "fuel": "natural_gas", "hours_permitted": 8760}


def _c(cid, field, value, value_num, quote):
    return {"id": cid, "field": field, "value": value, "value_num": value_num,
            "quote": quote, "entity_hint": None, "match_score": 1.0}


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


def test_entity_hint_binds_numeric_claim():
    claim = _c("c-h", "unit.hours_permitted", "8,760 hours", 8760,
               "increase annual operation to 8,760 hours per turbine")
    claim["entity_hint"] = "GE LM2500 turbine"
    links, updates, conflicts = plan_unit_changes([TITAN, LM], [claim])
    assert not updates and not conflicts
    assert [(u["id"], f) for u, f, _claim in links] == [
        ("u-lm", "hours_permitted")]


def test_oem_claim_binds_through_model_context():
    claim = _c("c-oem", "unit.oem", "Solar", None,
               "15 Solar SMT-130 gas turbines")
    smt = {**TITAN, "id": "u-smt", "model": "SMT-130",
           "oem": "Solar Turbines"}
    links, updates, conflicts = plan_unit_changes([smt], [claim])
    assert not updates and not conflicts
    assert [(u["id"], f) for u, f, _claim in links] == [("u-smt", "oem")]


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


PAGE = ("The permittee proposes changes to the Titan 350 simple-cycle "
        "turbine fleet. Specifically, the application would increase the "
        "site-rated power to 38 MW per turbine and adjust stack parameters "
        "accordingly. No changes are proposed for other emission units.")

PAGE_AMBIGUOUS = ("The site hosts Titan 350 and LM2500 machines. The "
                  "application would increase the site-rated power to 38 MW "
                  "per turbine for certain units at the facility.")


def test_locate_context_finds_window():
    ctx = locate_context(PAGE, "increase the site-rated power to 38 MW")
    assert ctx is not None and "titan 350" in ctx


def test_locate_context_rejects_absent_quote():
    assert locate_context(PAGE, "forty MegaMax 9000 units installed") is None


def test_context_binding_stages_update_when_quote_lacks_token():
    # The M5 known gap: quote names no model -> stayed unbound. With context
    # binding the surrounding sentence names exactly one model -> binds.
    claims = [_c("c11", "unit.mw_each", "38 MW", 38.0,
                 "increase the site-rated power to 38 MW per turbine")]
    ctx = {c["id"]: locate_context(PAGE, c["quote"]) for c in claims}
    links, updates, conflicts = plan_unit_changes(
        [TITAN, LM], claims, context_of=lambda c: ctx[c["id"]])
    assert not conflicts and not links
    assert updates["u-titan"]["mw_each"][0] == 38.0
    assert updates["u-titan"]["mw_each"][2] == "context"


def test_ambiguous_context_never_binds():
    claims = [_c("c12", "unit.mw_each", "38 MW", 38.0,
                 "increase the site-rated power to 38 MW per turbine")]
    ctx = {c["id"]: locate_context(PAGE_AMBIGUOUS, c["quote"])
           for c in claims}
    links, updates, conflicts = plan_unit_changes(
        [TITAN, LM], claims, context_of=lambda c: ctx[c["id"]])
    assert not links and not updates and not conflicts


def test_no_context_callable_keeps_m5_behavior():
    claims = [_c("c13", "unit.mw_each", "38 MW", 38.0,
                 "increase the site-rated power to 38 MW per turbine")]
    links, updates, conflicts = plan_unit_changes([TITAN, LM], claims)
    assert not links and not updates and not conflicts


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


def test_resolve_facility_uses_unique_reviewed_provenance_fallback():
    permits = [{"id": "p1", "permit_no": "0680-00119",
                "facility_id": "southaven"}]
    claims = [_c("court", "unit.mw_total", "495 MW", 495,
                 "combined generating capacity of at least 495 MW")]
    assert resolve_facility(claims, permits, {"southaven"}) == "southaven"
    assert resolve_facility(claims, permits, {"southaven", "memphis"}) is None


def test_sole_cohort_binding_retains_corroborated_historical_count():
    cohort = {**TITAN, "id": "cohort", "model": "unverified",
              "unit_count": 27, "mw_each": 18.333333, "total_mw": None}
    claims = [
        _c("old", "observation.unit_count", "18", 18,
           "18 gas-fired turbines had been built on site"),
        _c("current", "observation.unit_count", "27", 27,
           "27 turbines were still on site"),
        _c("later", "observation.unit_count", "57", 57,
           "satellite images revealed 57 turbines at the facility"),
        _c("mw", "unit.mw_total", "495 MW", 495,
           "27 turbines have a combined capacity of at least 495 MW"),
    ]
    links, updates, conflicts = plan_unit_changes([cohort], claims)
    assert [(field, claim["id"]) for _u, field, claim in links] == [
        ("unit_count", "current")]
    assert updates["cohort"]["total_mw"][0] == 495
    assert "unit_count" not in updates["cohort"]
    assert any("retained corroborated" in conflict for conflict in conflicts)


def test_resolve_permit_and_plan_exact_fields():
    permit = {"id": "p2", "permit_no": "01156-01PC", "facility_id": "f2",
              "authority": "Shelby County Health Department",
              "permit_type": "Air construction permit",
              "status": "issued; under appeal; CAA litigation",
              "filed_at": "2025-01-31", "issued_at": "2025-07-02"}
    claims = [
        _c("p-no", "permit.no", "01156-01PC", None,
           "Air Permit No. 01156-01PC"),
        _c("p-auth", "permit.authority", "Shelby County Health Department",
           None, "Shelby County Health Department issued the permit"),
        _c("p-date", "permit.issued_at", "July 2, 2025", None,
           "issued the permit on July 2, 2025"),
        _c("p-status", "permit.status", "issued", None,
           "issuance of Air Permit No. 01156-01PC"),
    ]
    assert resolve_permit(claims, [permit]) == permit
    links = plan_permit_links(permit, claims)
    got = {(field, method) for _p, field, _claim, method in links}
    assert got == {("permit_no", "exact permit number"),
                   ("authority", "text match"),
                   ("issued_at", "exact typed date"),
                   ("status", "status component")}


def test_plan_permit_changes_stages_one_unique_typed_value():
    permit = {"id": "p", "permit_no": "177263", "facility_id": "f",
              "authority": "TCEQ", "permit_type": None,
              "status": None, "filed_at": None, "issued_at": None}
    claims = [
        _c("no", "permit.no", "177263", None, "Registration 177263"),
        _c("type", "permit.type", "Standard Permit Application", None,
           "Project Type Standard Permit Application"),
        _c("status", "permit.status", "currently permitted", None,
           "units are currently permitted under Registration 177263"),
    ]
    links, changes, conflicts = plan_permit_changes(permit, claims)
    assert not conflicts
    assert {field for _p, field, _c, _m in links} == {"permit_no"}
    assert changes["permit_type"][0] == "Standard Permit Application"
    assert changes["status"][0] == "currently permitted"


def test_plan_permit_changes_never_overwrites_existing_field_from_one_claim():
    permit = {"id": "p", "permit_no": "177263", "facility_id": "f",
              "authority": "TCEQ", "permit_type": "EGU Standard Permit",
              "status": "issued", "filed_at": "2024-08-01",
              "issued_at": None}
    claims = [
        _c("authority", "permit.authority", "Texas", None,
           "Longhorn Data Center in Taylor County, Texas."),
        _c("status", "permit.status", "submitted", None,
           "The modification application was submitted."),
        _c("date", "permit.filed_at", "January 11, 2025", None,
           "Project Received Date January 11, 2025"),
    ]

    _links, changes, conflicts = plan_permit_changes(permit, claims)

    assert changes == {}
    assert len(conflicts) == 3
    assert all("was not overwritten" in conflict for conflict in conflicts)


def test_permit_date_mismatch_is_not_linked():
    permit = {"id": "p1", "permit_no": "177263", "facility_id": "f1",
              "authority": "TCEQ", "permit_type": "standard",
              "status": "issued", "filed_at": "2024-08-01",
              "issued_at": None}
    claim = _c("p-date", "permit.filed_at", "January 11, 2025", None,
               "Project Received Date January 11, 2025")
    assert plan_permit_links(permit, [claim]) == []


def test_legacy_generic_permit_date_is_never_bound():
    permit = {"id": "p1", "permit_no": "177263", "facility_id": "f1",
              "authority": "TCEQ", "permit_type": "standard",
              "status": "issued", "filed_at": "2025-01-11",
              "issued_at": None}
    claim = _c("legacy", "permit.date", "January 11, 2025", None,
               "Project Received Date January 11, 2025")
    assert plan_permit_links(permit, [claim]) == []


def test_staged_version_inherits_prior_receipts(monkeypatch):
    class Response:
        def json(self):
            return [{"fact_field": "model", "claim_id": "claim-1",
                     "note": "original receipt", "support_kind": "direct",
                     "derivation": None}]

    monkeypatch.setattr(normalize, "_rest",
                        lambda *_args, **_kwargs: Response())
    calls = []
    monkeypatch.setattr(
        normalize, "ensure_provenance",
        lambda *args: calls.append(args) or True,
    )

    assert copy_provenance("unit", "old", "staged") == 1
    assert calls == [
        ("unit", "staged", "model", "claim-1",
         "inherited from prior fact version: original receipt",
         "direct", None)]


def test_staged_copy_reuses_logical_identity_and_never_publishes(monkeypatch):
    calls = []

    class Response:
        def __init__(self, rows):
            self.rows = rows

        def json(self):
            return self.rows

    def fake_rest(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET":
            return Response([])
        return Response([{"id": "staged-id"}])

    monkeypatch.setattr(normalize, "_rest", fake_rest)
    unit = {**TITAN, "logical_id": "logical-1", "total_mw": None}
    sid = staged_copy(
        unit, {"unit_count": (5, {"id": "claim"}, "quote")},
        basis="permitted", verification_state="source_asserted")

    assert sid == "staged-id"
    posted = calls[-1][2]["json"][0]
    assert posted["logical_id"] == "logical-1"
    assert posted["unit_count"] == 5
    assert posted["fact_state"] == "staging"
    assert posted["basis"] == "permitted"
    assert posted["verification_state"] == "source_asserted"


def test_permit_quantities_win_over_unrelated_observation_for_basis():
    permit_count = {
        **_c("permit-count", "unit.count", "5", 5,
             "The permit authorizes five Titan 350 turbines."),
        "document": {"doc_genre": "permit"},
    }
    satellite_total = {
        **_c("satellite-mw", "observation.mw", "190 MW", 190,
             "Estimated visible generation capacity is 190 MW."),
        "document": {"doc_genre": "satellite_scene"},
    }

    basis, derivation = _unit_basis([satellite_total, permit_count])

    assert basis == "permitted"
    assert "regulatory" in derivation
    assert _basis_claim([satellite_total, permit_count], basis) == permit_count
    assert _unit_receipt_compatible(basis, "unit_count", permit_count)


def test_observed_cohort_stays_observed_when_model_metadata_is_present():
    observed_count = _c(
        "observed-count", "observation.unit_count", "27", 27,
        "Twenty-seven generator enclosures are visible.")
    model = _c(
        "model", "unit.model", "FlexTitan", None,
        "The equipment resembles FlexTitan packages.")

    basis, _derivation = _unit_basis([model, observed_count])

    assert basis == "observed"
    assert _basis_claim([model, observed_count], basis) == observed_count
    assert _unit_receipt_compatible(basis, "unit_count", observed_count)
    assert not _unit_receipt_compatible(
        basis, "unit_count", _c("permit", "unit.count", "27", 27, "27"))


def test_reported_quantitative_claim_is_not_reclassified_by_observation():
    reported_count = {
        **_c("reported", "unit.count", "15", 15,
             "The filing reports fifteen generator units."),
        "document": {"doc_genre": "court_filing"},
    }
    observation = {
        **_c("observation", "observation.mw", "250 MW", 250,
             "The scene is consistent with roughly 250 MW."),
        "document": {"doc_genre": "satellite_scene"},
    }

    basis, _derivation = _unit_basis([observation, reported_count])

    assert basis == "reported"
    assert _basis_claim([observation, reported_count], basis) == reported_count
