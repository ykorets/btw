"""M3.5 — cross-page anchoring + quorum verdict tests (pure functions)."""

from btw_engine.extract import (
    MATCH_THRESHOLD,
    _values_match,
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
