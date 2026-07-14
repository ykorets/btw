"""Read-only truth audit for the currently published mirror inputs."""

from btw_engine import publish
from btw_engine.truth import provenance_violations


def main() -> None:
    facilities, _events, _aggregates, _announcements = publish.fetch()
    violations = provenance_violations(facilities,
                                       publish.fetch_provenance())
    if not violations:
        print("truth audit: unit/permit provenance is complete")
        return
    print(f"truth audit: {len(violations)} violation(s)")
    for violation in violations:
        print(f"- {violation}")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
