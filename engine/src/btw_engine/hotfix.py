"""btw hotfix — one event + evidence → staged event + instant review PR (M8).

The hot lane: breaking developments don't wait for the morning sweep.
Inserts a fact_state='staging' event and immediately opens (or refreshes)
today's review PR; merging promotes it and regenerates the mirror.

Usage (also exposed as a workflow_dispatch with inputs):
  python -m btw_engine.hotfix --facility xai-colossus-1 \
      --date 2026-07-11 --type enforcement \
      --headline "SCHD issues NOV over unpermitted units" \
      [--url https://evidence...]

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, GITHUB_TOKEN, GITHUB_REPOSITORY.
"""

import argparse

from btw_engine.review import _rest, open_pr


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--facility", help="facility slug (optional)")
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--type", required=True, dest="event_type")
    ap.add_argument("--headline", required=True)
    ap.add_argument("--url", default=None,
                    help="evidence URL, appended to the headline")
    args = ap.parse_args()

    facility_id = None
    if args.facility:
        rows = _rest("GET", "facility", params={
            "select": "id", "slug": f"eq.{args.facility}"}).json()
        if not rows:
            raise SystemExit(f"unknown facility slug: {args.facility}")
        facility_id = rows[0]["id"]

    headline = args.headline
    if args.url:
        headline += f" [{args.url}]"

    _rest("POST", "event", json=[{
        "facility_id": facility_id,
        "event_date": args.date,
        "event_type": args.event_type,
        "headline": headline,
        "fact_state": "staging",
    }])
    print(f"staged event: {args.date} {args.event_type} — {headline[:80]}")
    open_pr()


if __name__ == "__main__":
    main()
