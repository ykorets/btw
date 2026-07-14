"""Verify that a mirror export remains backward-compatible with v1.

The contract intentionally allows additive JSON fields. It protects the
existing public file names, top-level envelopes, nested row fields, and CSV
column order consumed by BTW pages, agents, and downstream datasets.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


DEFAULT_CONTRACT = Path(__file__).resolve().parents[2] / "contracts/mirror-v1.json"


def _missing(row: dict, required: list[str]) -> list[str]:
    return sorted(set(required) - set(row))


def _rows(payload: dict, path: str) -> list[dict]:
    if "[]." not in path:
        value = payload.get(path)
        return value if isinstance(value, list) else []
    parent, child = path.split("[].", 1)
    out: list[dict] = []
    for row in payload.get(parent, []):
        value = row.get(child, []) if isinstance(row, dict) else []
        if isinstance(value, list):
            out.extend(item for item in value if isinstance(item, dict))
    return out


def check(data_dir: str | Path, contract_path: str | Path = DEFAULT_CONTRACT
          ) -> list[str]:
    root = Path(data_dir)
    contract = json.loads(Path(contract_path).read_text())
    violations: list[str] = []
    for name, spec in contract["files"].items():
        path = root / name
        if not path.is_file():
            violations.append(f"missing public file: {name}")
            continue
        payload = json.loads(path.read_text())
        missing = _missing(payload, spec["top"])
        if missing:
            violations.append(f"{name}: missing top-level {', '.join(missing)}")
        for collection, required in spec["collections"].items():
            rows = _rows(payload, collection)
            for index, row in enumerate(rows):
                missing = _missing(row, required)
                if missing:
                    violations.append(
                        f"{name}:{collection}[{index}] missing "
                        f"{', '.join(missing)}")
    for name, header in contract["csv"].items():
        path = root / name
        if not path.is_file():
            violations.append(f"missing public file: {name}")
            continue
        with path.open(newline="") as fp:
            actual = next(csv.reader(fp), [])
        if actual != header:
            violations.append(
                f"{name}: header changed: expected {header!r}, got {actual!r}")
    return violations


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    args = parser.parse_args()
    violations = check(args.data, args.contract)
    if violations:
        detail = "\n".join(f"- {item}" for item in violations)
        raise SystemExit(
            f"mirror v1 compatibility failed ({len(violations)}):\n{detail}")
    print("mirror v1 compatibility: ok")


if __name__ == "__main__":
    main()
