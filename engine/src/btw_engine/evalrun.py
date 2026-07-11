"""btw_engine.evalrun — score extraction against hand-labeled expectations.

Recall: share of expected facts found among VALIDATED claims of the doc.
Precision proxy: validated / total claims the extractor produced for the doc.
Prints a per-document table and exits non-zero if overall recall < 0.6
(kill-criteria guard from docs/plan.md).

Usage: python -m btw_engine.evalrun [--expected engine/evals/genesis_expected.yaml]
"""

import argparse
import os
import sys

import httpx
import yaml


def _rest(path: str, params: dict) -> list[dict]:
    base = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/" + path
    key = os.environ["SUPABASE_SERVICE_KEY"]
    r = httpx.get(base, headers={"apikey": key, "Authorization": f"Bearer {key}"},
                  params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def matches(exp: dict, claims: list[dict]) -> bool:
    for c in claims:
        if c["field"] != exp["field"]:
            continue
        if "value_num" in exp:
            try:
                if c.get("value_num") is not None and \
                   abs(float(c["value_num"]) - float(exp["value_num"])) <= \
                   max(0.001, abs(float(exp["value_num"])) * 0.001):
                    return True
            except (TypeError, ValueError):
                continue
        elif "value_contains" in exp:
            if exp["value_contains"].lower() in (c.get("value") or "").lower():
                return True
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expected", default="engine/evals/genesis_expected.yaml")
    args = ap.parse_args()

    specs = yaml.safe_load(open(args.expected))
    total_exp, total_found = 0, 0
    print(f"{'document':38} {'recall':>10} {'precision':>10} {'claims':>7}")
    for spec in specs:
        docs = _rest("document", {"select": "id,url",
                                  "url": f"like.*{spec['match']}*"})
        if not docs:
            print(f"{spec['name']:38} {'no doc':>10}")
            continue
        doc_id = docs[0]["id"]
        all_claims = _rest("claim", {
            "select": "field,value,value_num,status",
            "document_id": f"eq.{doc_id}"})
        validated = [c for c in all_claims if c["status"] == "validated"]
        found = sum(matches(e, validated) for e in spec["expected"])
        total_exp += len(spec["expected"])
        total_found += found
        recall = found / len(spec["expected"])
        precision = (len(validated) / len(all_claims)) if all_claims else 0.0
        print(f"{spec['name']:38} {found}/{len(spec['expected']):>2} "
              f"({recall:.0%}) {precision:>9.0%} {len(all_claims):>7}")

    overall = total_found / total_exp if total_exp else 0.0
    print(f"\nOVERALL RECALL: {total_found}/{total_exp} ({overall:.0%})")
    if overall < 0.6:
        sys.exit("KILL-CRITERIA GUARD: recall < 60% — rethink extraction "
                 "before building watchers (docs/plan.md).")


if __name__ == "__main__":
    main()
