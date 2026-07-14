import csv
import json

from btw_engine.compat import check


def _write_contract(tmp_path):
    contract = {
        "files": {"facilities.json": {
            "top": ["license", "facilities"],
            "collections": {
                "facilities": ["slug", "unit"],
                "facilities[].unit": ["unit_count"],
            },
        }},
        "csv": {"fleet.csv": ["slug", "mw"]},
    }
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract))
    return path


def test_compatibility_contract_allows_additive_fields(tmp_path):
    contract = _write_contract(tmp_path)
    (tmp_path / "facilities.json").write_text(json.dumps({
        "license": "CC BY 4.0",
        "facilities": [{"slug": "alpha", "unit": [
            {"unit_count": 5, "basis": "permitted"}], "new_field": True}],
        "new_envelope_field": "allowed",
    }))
    with (tmp_path / "fleet.csv").open("w", newline="") as fp:
        csv.writer(fp).writerow(["slug", "mw"])

    assert check(tmp_path, contract) == []


def test_compatibility_contract_reports_nested_and_csv_breaks(tmp_path):
    contract = _write_contract(tmp_path)
    (tmp_path / "facilities.json").write_text(json.dumps({
        "license": "CC BY 4.0",
        "facilities": [{"slug": "alpha", "unit": [{}]}],
    }))
    with (tmp_path / "fleet.csv").open("w", newline="") as fp:
        csv.writer(fp).writerow(["mw", "slug"])

    violations = check(tmp_path, contract)

    assert any("unit_count" in item for item in violations)
    assert any("header changed" in item for item in violations)
