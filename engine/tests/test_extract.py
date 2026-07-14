"""M3.5 — cross-page anchoring + quorum verdict tests (pure functions)."""

from btw_engine.extract import (
    MATCH_THRESHOLD,
    _values_match,
    canonical_claims,
    deterministic_claims,
    html_text,
    quorum_verdicts,
    validate_claim,
)

# A quote that straddles the page 1 → 2 break: regulatory filings routinely
# split sentences across pages, which is exactly what sank the two appeal
# claims in the M3 baseline (unit.count 15, SMT-130).
PAGE1 = ("The applicant seeks authorization to operate fifteen (15) "
         "Solar Turbines")
PAGE2 = ("SMT-130 generator sets with a combined nameplate capacity at the "
         "Memphis facility. Permit No. 01156-01PC governs this equipment.")
PAGES = [PAGE1, PAGE2, "Unrelated appendix text with the number 99."]

SPANNING_QUOTE = ("authorization to operate fifteen (15) Solar Turbines "
                  "SMT-130 generator sets")


def _claim(quote, page, value_num=None):
    return {"quote": quote, "page": page, "value_num": value_num}


def test_same_page_quote_matches_with_zero_offset():
    score, num_ok, offset = validate_claim(
        _claim("Permit No. 01156-01PC governs this equipment", 2), PAGES)
    assert score >= MATCH_THRESHOLD
    assert offset == 0


def test_cross_page_quote_rescued_by_span():
    # On the claimed page alone the quote fails the threshold…
    score1, _n1, off1 = validate_claim(_claim(SPANNING_QUOTE, 1), PAGES)
    assert score1 >= MATCH_THRESHOLD and off1 == +1
    # …and from the other side of the break too.
    score2, _n2, off2 = validate_claim(_claim(SPANNING_QUOTE, 2), PAGES)
    assert score2 >= MATCH_THRESHOLD and off2 == -1


def test_cross_page_numeric_check_uses_matched_span():
    # value 15 appears only on page 1; claim anchored to page 2 must still
    # pass the numeric check because the matched text is the page 1+2 span.
    score, num_ok, offset = validate_claim(
        _claim(SPANNING_QUOTE, 2, value_num=15), PAGES)
    assert score >= MATCH_THRESHOLD
    assert num_ok is True
    assert offset == -1


def test_hallucinated_quote_still_rejected():
    score, _n, _o = validate_claim(
        _claim("the facility will install forty MegaMax 9000 units", 1),
        PAGES)
    assert score < MATCH_THRESHOLD


def test_number_absent_everywhere_fails_numeric_check():
    _s, num_ok, _o = validate_claim(
        _claim("Permit No. 01156-01PC governs this equipment", 2,
               value_num=777), PAGES)
    assert num_ok is False


# ---- quorum (D9) --------------------------------------------------------

def _q(field, value, value_num, quote):
    return {"field": field, "value": value, "value_num": value_num,
            "quote": quote}


def test_quorum_agree_on_numeric_value():
    p = [_q("unit.count", "15", 15, "operate fifteen (15) Solar Turbines")]
    c = [_q("unit.count", "fifteen", 15.0, "fifteen (15) Solar Turbines "
            "SMT-130 generator sets")]
    assert quorum_verdicts(p, c) == ["agree"]


def test_quorum_disagree_same_passage_different_value():
    p = [_q("unit.count", "15", 15, "operate fifteen (15) Solar Turbines "
            "SMT-130 generator sets")]
    c = [_q("unit.count", "16", 16, "operate fifteen (15) Solar Turbines "
            "SMT-130 generator sets")]
    assert quorum_verdicts(p, c) == ["disagree"]


def test_quorum_solo_when_checker_silent():
    p = [_q("unit.mw_each", "34.1", 34.1, "rated at 34.1 MW each")]
    c = [_q("permit.no", "177263", None, "Permit No. 177263")]
    assert quorum_verdicts(p, c) == ["solo"]


def test_quorum_same_field_different_unit_is_solo_not_disagree():
    # Two units, two legitimate mw_each values anchored to DIFFERENT
    # passages: coverage variance, not contradiction.
    p = [_q("unit.mw_each", "38", 38.0,
            "site-rated power to 38 MW per Titan 350 turbine")]
    c = [_q("unit.mw_each", "34.1", 34.1,
            "the GE LM2500 units are rated at 34.1 MW each")]
    assert quorum_verdicts(p, c) == ["solo"]


def test_values_match_text_containment():
    a = _q("unit.model", "SMT-130", None, "q1")
    b = _q("unit.model", "Solar Turbines SMT-130", None, "q2")
    assert _values_match(a, b)
    assert not _values_match(a, _q("unit.model", "LM2500", None, "q3"))


def test_canonical_claims_types_explicit_legacy_date():
    source = {"field": "permit.date", "value": "March 11, 2026",
              "value_num": None,
              "quote": "MDEQ issued MZX a permit on March 11, 2026.",
              "page": 6, "entity_hint": "permit"}
    got = canonical_claims([source])
    typed = [c for c in got if c["field"] == "permit.issued_at"]
    assert len(typed) == 1
    assert typed[0]["value"] == "March 11, 2026"
    assert typed[0]["quote"] == source["quote"]


def test_canonical_claims_extracts_explicit_gas_fuel_once():
    source = {"field": "observation.unit_count", "value": "27",
              "value_num": 27,
              "quote": "Twenty-seven gas-fired turbines were on site.",
              "page": 7, "entity_hint": "observation"}
    got = canonical_claims([source, source])
    fuels = [c for c in got if c["field"] == "unit.fuel"]
    assert len(fuels) == 1
    assert fuels[0]["value"] == "natural gas"


def test_deterministic_tceq_claims_are_explicit_and_permit_scoped():
    page = """
    Electric Generating Unit Standard Permit Technical Review
    Registration Number 177263
    The Texas Commission on Environmental Quality (TCEQ) has determined
    that the emissions are authorized.
    The units are currently permitted under Standard Permit Registration 177263.
    """

    got = deterministic_claims([page])
    fields = {(c["field"], c["value"]) for c in got}

    assert ("permit.authority", "TCEQ") in fields
    assert ("permit.type", "EGU Standard Permit registration") in fields
    assert ("permit.status", "issued") in fields
    assert all(c["entity_hint"] == "permit 177263" for c in got)


def test_deterministic_schd_claims_cover_every_status_component():
    page = """
    RE: Appeal of Air Permit No. 01156-01PC
    Shelby County Health Department’s (“SCHD” or “Health Department”)
    issuance of Air Permit No. 01156-01PC.
    Response to Public Comments on Draft Construction Air Permit No. 01156-01PC.
    """
    second = "when the Health Department issued the CTC Permit on July 2, 2025"

    got = deterministic_claims([page, second])
    fields = {(c["field"], c["value"]) for c in got}

    assert ("permit.authority", "Shelby County Health Department") in fields
    assert ("permit.type", "Air construction permit") in fields
    assert ("permit.status", "issued") in fields
    assert ("permit.status", "under appeal") in fields
    assert ("permit.issued_at", "July 2, 2025") in fields


def test_deterministic_mdeq_pdf_claims_type_and_issue_date():
    pages = [
        "Permit No.: 0680-00119",
        "PSD Air Construction Permit No.: 0680-00119",
        "MDEQ issued MZX a permit on March 11, 2026.",
    ]

    got = deterministic_claims(pages)
    fields = {(c["field"], c["value"]) for c in got}

    assert ("permit.type", "PSD Air Construction Permit") in fields
    assert ("permit.status", "issued") in fields
    assert ("permit.issued_at", "March 11, 2026") in fields


def test_deterministic_mdeq_registry_html_uses_archived_visible_text():
    body = b"""<html><script>ignore()</script><body>
      <table><tr><td>Air - Air-Construction PSD</td>
      <td>0680-00119</td><td>03/11/2026</td><td>Permit Issued</td></tr></table>
      </body></html>"""

    got = deterministic_claims([html_text(body)])
    fields = {(c["field"], c["value"]) for c in got}

    assert ("permit.no", "0680-00119") in fields
    assert ("permit.type", "PSD Air Construction Permit") in fields
    assert ("permit.issued_at", "03/11/2026") in fields
    assert ("permit.status", "issued") in fields
