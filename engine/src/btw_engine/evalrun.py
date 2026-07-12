"""btw_engine.evalrun — score extraction against hand-labeled expectations.

Recall: share of expected facts found among VALIDATED claims of the doc.
Precision proxy: validated / total claims the extractor produced for the doc.
Prints a per-document table and exits non-zero if overall recall < 0.6
(kill-criteria guard from docs/plan.md).

M3.5 per-model comparison (D8/D9 — evals pick role assignments):
  --version <extractor_version>   score only claims of that version
  --compare                       one row per extractor_version seen on the
                                  eval docs: recall, precision, cost (summed
                                  from the llm_call ledger per model)

Usage:
  python -m btw_engine.evalrun [--expected engine/evals/genesis_expected.yaml]
  python -m btw_engine.evalrun --compare
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


def _load_docs(specs: list[dict]) -> dict[str, str]:
    """spec name -> document id (first url match)."""
    out = {}
    for spec in specs:
        docs = _rest("document", {"select": "id,url",
                                  "url": f"like.*{spec['match']}*"})
        if docs:
            out[spec["name"]] = docs[0]["id"]
    return out


def score(specs: list[dict], doc_ids: dict[str, str],
          version: str | None) -> tuple[int, int]:
    total_exp, total_found = 0, 0
    print(f"{'document':38} {'recall':>10} {'precision':>10} {'claims':>7}")
    for spec in specs:
        doc_id = doc_ids.get(spec["name"])
        if doc_id is None:
            print(f"{spec['name']:38} {'no doc':>10}")
            continue
        params = {"select": "field,value,value_num,status,extractor_version",
                  "document_id": f"eq.{doc_id}"}
        if version:
            params["extractor_version"] = f"eq.{version}"
        all_claims = _rest("claim", params)
        validated = [c for c in all_claims if c["status"] == "validated"]
        found = sum(matches(e, validated) for e in spec["expected"])
        total_exp += len(spec["expected"])
        total_found += found
        recall = found / len(spec["expected"])
        precision = (len(validated) / len(all_claims)) if all_claims else 0.0
        print(f"{spec['name']:38} {found}/{len(spec['expected']):>2} "
              f"({recall:.0%}) {precision:>9.0%} {len(all_claims):>7}")
    return total_found, total_exp


def compare(specs: list[dict], doc_ids: dict[str, str]) -> None:
    """Per-extractor_version table across the eval docs, with ledger cost
    per model. Picks role assignments per D8/D9 — read, then edit
    engine/models.yaml; assignment stays config, not code."""
    ids = list(doc_ids.values())
    id_list = ",".join(ids)
    claims = _rest("claim", {
        "select": "document_id,field,value,value_num,status,extractor_version",
        "document_id": f"in.({id_list})"})
    versions = sorted({c["extractor_version"] for c in claims})
    if not versions:
        sys.exit("no claims on eval docs — run extract first")

    ledger = _rest("llm_call", {
        "select": "model,cost_usd,document_id,purpose",
        "purpose": "eq.extract",
        "document_id": f"in.({id_list})"})
    cost_by_model: dict[str, float] = {}
    for row in ledger:
        cost_by_model[row["model"]] = (cost_by_model.get(row["model"], 0.0)
                                       + (row["cost_usd"] or 0.0))

    print(f"{'extractor_version':52} {'recall':>9} {'precision':>10} "
          f"{'claims':>7}")
    for v in versions:
        v_claims = [c for c in claims if c["extractor_version"] == v]
        validated = [c for c in v_claims if c["status"] == "validated"]
        by_doc: dict[str, list[dict]] = {}
        for c in validated:
            by_doc.setdefault(c["document_id"], []).append(c)
        exp_n = found = 0
        for spec in specs:
            doc_id = doc_ids.get(spec["name"])
            if doc_id is None:
                continue
            doc_validated = by_doc.get(doc_id, [])
            exp_n += len(spec["expected"])
            found += sum(matches(e, doc_validated) for e in spec["expected"])
        recall = found / exp_n if exp_n else 0.0
        precision = len(validated) / len(v_claims) if v_claims else 0.0
        print(f"{v:52} {found}/{exp_n:>2} ({recall:.0%}) {precision:>9.0%} "
              f"{len(v_claims):>7}")

    print("\nledger cost per model (purpose=extract, eval docs, all runs):")
    for model, cost in sorted(cost_by_model.items()):
        print(f"  {model:48} ${cost:.4f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expected", default="engine/evals/genesis_expected.yaml")
    ap.add_argument("--version", help="score only this extractor_version")
    ap.add_argument("--compare", action="store_true",
                    help="per-extractor_version recall/precision/cost table")
    args = ap.parse_args()

    specs = yaml.safe_load(open(args.expected))
    doc_ids = _load_docs(specs)

    if args.compare:
        compare(specs, doc_ids)
        return

    total_found, total_exp = score(specs, doc_ids, args.version)
    overall = total_found / total_exp if total_exp else 0.0
    print(f"\nOVERALL RECALL: {total_found}/{total_exp} ({overall:.0%})")
    if overall < 0.6:
        sys.exit("KILL-CRITERIA GUARD: recall < 60% — rethink extraction "
                 "before building watchers (docs/plan.md).")


if __name__ == "__main__":
    main()
