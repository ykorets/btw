"""Immutable review manifests and atomic promotion.

``--open`` snapshots the exact staged unit, permit, and event row ids into a
sealed database manifest, embeds its id/hash in the daily review file, and
opens the human-review PR. ``--promote`` reads those two values from the
merged file and invokes one database transaction. It never enumerates or
patches whichever rows happen to be staging at merge time.

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY; for --open also GITHUB_TOKEN and
GITHUB_REPOSITORY.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import sys
import uuid
from pathlib import Path

import httpx

from btw_engine.truth import provenance_violations

GH_API = "https://api.github.com"
REVIEW_ID_RE = re.compile(r"^<!-- BTW_REVIEW_ID: ([0-9a-fA-F-]{36}) -->$", re.M)
MANIFEST_HASH_RE = re.compile(r"^<!-- BTW_MANIFEST_HASH: ([0-9a-f]{64}) -->$", re.M)


def _rest(method: str, path: str, **kw) -> httpx.Response:
    base = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/" + path
    key = os.environ["SUPABASE_SERVICE_KEY"]
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    headers.update(kw.pop("headers", {}))
    response = httpx.request(method, base, headers=headers, timeout=60, **kw)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = (response.text or "").strip().replace("\n", " ")[:1000]
        raise RuntimeError(
            f"Supabase {method} {path} failed "
            f"({response.status_code}): {detail}"
        ) from exc
    return response


def _gh(method: str, path: str, **kw) -> httpx.Response:
    headers = {
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
    }
    return httpx.request(method, GH_API + path, headers=headers,
                         timeout=30, **kw)


def _provenance(fact_table: str, fact_id: str) -> list[dict]:
    return _rest("GET", "fact_provenance", params={
        "select": "fact_table,fact_id,fact_field,note,support_kind,derivation,"
                  "claim:claim_id(field,value,value_num,status,anchor,quote,"
                  "page,bbox,match_score,numeric_check,"
                  "document:document_id(url,r2_key))",
        "fact_table": f"eq.{fact_table}", "fact_id": f"eq.{fact_id}",
    }).json()


# ------------------------------------------------------------------- diff --

def staged_units() -> list[dict]:
    staged = _rest("GET", "unit", params={
        "select": "id,logical_id,facility_id,oem,model,unit_count,mw_each,"
                  "total_mw,fuel,hours_permitted,basis,verification_state,"
                  "facility(slug,name)",
        "fact_state": "eq.staging",
    }).json()
    out = []
    for row in staged:
        published = _rest("GET", "unit", params={
            "select": "id,logical_id,oem,model,unit_count,mw_each,total_mw,"
                      "fuel,hours_permitted,basis,verification_state",
            "logical_id": f"eq.{row['logical_id']}",
            "fact_state": "eq.published",
        }).json()
        out.append({"staged": row,
                    "published": published[0] if published else None,
                    "provenance": _provenance("unit", row["id"])})
    return out


def staged_permits() -> list[dict]:
    staged = _rest("GET", "permit", params={
        "select": "id,logical_id,facility_id,authority,permit_no,permit_type,"
                  "status,filed_at,issued_at,verification_state,"
                  "facility(slug,name)",
        "fact_state": "eq.staging",
    }).json()
    out = []
    for row in staged:
        published = _rest("GET", "permit", params={
            "select": "id,logical_id,authority,permit_no,permit_type,status,"
                      "filed_at,issued_at,verification_state",
            "logical_id": f"eq.{row['logical_id']}",
            "fact_state": "eq.published",
        }).json()
        out.append({"staged": row,
                    "published": published[0] if published else None,
                    "provenance": _provenance("permit", row["id"])})
    return out


def staged_events() -> list[dict]:
    return _rest("GET", "event", params={
        "select": "id,event_date,event_type,headline,source_url,facility(slug)",
        "fact_state": "eq.staging", "order": "event_date.desc",
    }).json()


def fresh_candidates(hours: int = 36) -> list[dict]:
    since = (dt.datetime.now(dt.timezone.utc)
             - dt.timedelta(hours=hours)).isoformat()
    return _rest("GET", "candidate", params={
        "select": "external_id,title,url,payload,source_id",
        "found_at": f"gt.{since}", "payload->>kw_hit": "eq.true",
        "order": "found_at.desc",
    }).json()


def partition_review_ready(units: list[dict], permits: list[dict],
                           events: list[dict]) -> tuple[
                               list[dict], list[dict], list[dict], list[dict]]:
    """Hold incomplete staging rows out of a manifest without hiding them."""
    ready_units, ready_permits, ready_events, held = [], [], [], []
    for item in units:
        staged = item["staged"]
        facility = staged.get("facility") or {}
        violations = provenance_violations([{
            "slug": facility.get("slug") or staged["facility_id"],
            "unit": [staged], "permit": [],
        }], item["provenance"])
        if violations:
            held.append({"kind": "unit", "id": staged["id"],
                         "label": staged.get("model") or staged["id"],
                         "violations": violations})
        else:
            ready_units.append(item)
    for item in permits:
        staged = item["staged"]
        facility = staged.get("facility") or {}
        violations = provenance_violations([{
            "slug": facility.get("slug") or staged["facility_id"],
            "unit": [], "permit": [staged],
        }], item["provenance"])
        if violations:
            held.append({"kind": "permit", "id": staged["id"],
                         "label": staged.get("permit_no") or staged["id"],
                         "violations": violations})
        else:
            ready_permits.append(item)
    for event in events:
        if not str(event.get("source_url") or "").strip():
            held.append({"kind": "event", "id": event["id"],
                         "label": event.get("headline") or event["id"],
                         "violations": ["event has no source_url"]})
        else:
            ready_events.append(event)
    return ready_units, ready_permits, ready_events, held


def create_review_manifest(day: str, units: list[dict], permits: list[dict],
                           events: list[dict]) -> dict:
    """Seal the exact staged row versions. Safe to retry with the same set."""
    payload = {
        "p_batch_date": day,
        "p_unit_ids": sorted(row["staged"]["id"] for row in units),
        "p_permit_ids": sorted(row["staged"]["id"] for row in permits),
        "p_event_ids": sorted(row["id"] for row in events),
    }
    rows = _rest("POST", "rpc/btw_create_review_manifest", json=payload).json()
    if not isinstance(rows, list) or len(rows) != 1:
        raise RuntimeError(f"manifest RPC returned an unexpected payload: {rows!r}")
    manifest = rows[0]
    uuid.UUID(manifest["review_id"])
    if not re.fullmatch(r"[0-9a-f]{64}", manifest["manifest_hash"]):
        raise RuntimeError("manifest RPC returned an invalid SHA-256 hash")
    return manifest


def _render_receipts(lines: list[str], provenance: list[dict]) -> None:
    for receipt in provenance:
        claim = receipt.get("claim") or {}
        document = claim.get("document") or {}
        quote = (claim.get("quote") or "").strip()
        support = receipt.get("support_kind") or "direct"
        if quote:
            lines.append(f"> {quote}")
        else:
            lines.append("> Structured source anchor (cell or figure)")
        source = document.get("url") or ""
        lines.append(
            f"> — `{receipt['fact_field']}` ({support}), match "
            f"{claim.get('match_score')}, [source]({source})")
        if receipt.get("derivation"):
            lines.append(f"> — derivation: {receipt['derivation']}")
        lines.append("")


def render(units: list[dict], permits: list[dict], cands: list[dict], day: str,
           manifest: dict, events: list[dict] | None = None,
           held: list[dict] | None = None) -> str:
    lines = [
        f"<!-- BTW_REVIEW_ID: {manifest['review_id']} -->",
        f"<!-- BTW_MANIFEST_HASH: {manifest['manifest_hash']} -->",
        f"# Review — {day}", "",
        "This PR approves only the exact immutable row versions listed in "
        "the sealed manifest above.", "",
    ]
    if events:
        lines += ["## Staged events (hot lane)", ""]
        for event in events:
            facility = (event.get("facility") or {}).get("slug", "—")
            lines.append(
                f"- **{event['event_date']}** [{event['event_type']}] "
                f"{facility}: {event['headline']} "
                f"([source]({event.get('source_url') or ''}))")
        lines.append("")

    if units:
        lines += ["## Staged unit fact versions", ""]
        fields = ("oem", "model", "unit_count", "mw_each", "total_mw",
                  "fuel", "hours_permitted", "basis", "verification_state")
        for item in units:
            staged, published = item["staged"], item["published"]
            facility = (staged.get("facility") or {}).get("slug", "?")
            lines += [f"### {facility} — {staged.get('model') or 'unit'}", ""]
            for field in fields:
                old = published.get(field) if published else None
                new = staged.get(field)
                if published is None or new != old:
                    lines.append(f"- **{field}**: {old!r} → **{new!r}**")
            lines.append("")
            _render_receipts(lines, item["provenance"])

    if permits:
        lines += ["## Staged permit fact versions", ""]
        fields = ("authority", "permit_no", "permit_type", "status",
                  "filed_at", "issued_at", "verification_state")
        for item in permits:
            staged, published = item["staged"], item["published"]
            facility = (staged.get("facility") or {}).get("slug", "?")
            lines += [f"### {facility} — permit {staged['permit_no']}", ""]
            for field in fields:
                old = published.get(field) if published else None
                new = staged.get(field)
                if published is None or new != old:
                    lines.append(f"- **{field}**: {old!r} → **{new!r}**")
            lines.append("")
            _render_receipts(lines, item["provenance"])

    if cands:
        lines += ["## New keyword candidates (informational)", ""]
        for candidate in cands:
            lines.append(
                f"- `{candidate['source_id']}` #{candidate['external_id']}: "
                f"{candidate['title']}")
        lines.append("")
    if held:
        lines += ["## Held back by the truth gate (not in this manifest)", ""]
        for item in held:
            lines.append(f"- **{item['kind']} {item['label']}**")
            for violation in item["violations"]:
                lines.append(f"  - {violation}")
        lines.append("")
    lines += [
        "---",
        "Merging this PR promotes only this manifest in one database "
        "transaction. Candidates are informational and are not promoted.", "",
    ]
    return "\n".join(lines)


def parse_manifest(text: str) -> tuple[str, str]:
    id_match = REVIEW_ID_RE.search(text)
    hash_match = MANIFEST_HASH_RE.search(text)
    if not id_match or not hash_match:
        raise ValueError("review file has no valid sealed manifest metadata")
    review_id, manifest_hash = id_match.group(1).lower(), hash_match.group(1)
    uuid.UUID(review_id)
    return review_id, manifest_hash


# ------------------------------------------------------------------- open --

def open_pr() -> None:
    day = dt.date.today().isoformat()
    units = staged_units()
    permits = staged_permits()
    events = staged_events()
    candidates = fresh_candidates()
    if not units and not permits and not events and not candidates:
        print("nothing to review today")
        return

    units, permits, events, held = partition_review_ready(
        units, permits, events)
    for item in held:
        print(f"HOLD {item['kind']} {item['label']}: "
              f"{len(item['violations'])} truth violation(s)")
    if not units and not permits and not events and not candidates:
        print("nothing review-ready today")
        return

    manifest = create_review_manifest(day, units, permits, events)
    body = render(units, permits, candidates, day, manifest, events, held)
    repo = os.environ["GITHUB_REPOSITORY"]
    branch = f"review/{day}"

    main_sha = _gh("GET", f"/repos/{repo}/git/ref/heads/main"
                   ).json()["object"]["sha"]
    response = _gh("POST", f"/repos/{repo}/git/refs",
                   json={"ref": f"refs/heads/{branch}", "sha": main_sha})
    if response.status_code not in (201, 422):
        sys.exit(f"branch create failed: {response.status_code} "
                 f"{response.text[:200]}")

    path = f"review/{day}.md"
    previous = _gh("GET", f"/repos/{repo}/contents/{path}",
                   params={"ref": branch})
    payload = {
        "message": f"review: {day}",
        "content": base64.b64encode(body.encode()).decode(),
        "branch": branch,
    }
    if previous.status_code == 200:
        payload["sha"] = previous.json()["sha"]
    response = _gh("PUT", f"/repos/{repo}/contents/{path}", json=payload)
    if response.status_code not in (200, 201):
        sys.exit(f"file put failed: {response.status_code} {response.text[:200]}")

    response = _gh("POST", f"/repos/{repo}/pulls", json={
        "title": f"Review {day}: {len(units)} unit(s), "
                 f"{len(permits)} permit(s), {len(events)} event(s)",
        "head": branch, "base": "main",
        "body": "Daily evidence review. **Merge = approve the sealed "
                "manifest**; promotion is atomic and fail-closed.",
    })
    if response.status_code == 201:
        pr_url = response.json()["html_url"]
        _rest("PATCH", "review", params={"id": f"eq.{manifest['review_id']}"},
              json={"pr_url": pr_url})
        print("review PR opened:", pr_url)
    elif response.status_code == 422:
        print("review PR already open for", branch)
    else:
        sys.exit(f"PR create failed: {response.status_code} {response.text[:200]}")

    if os.environ.get("AUTOMERGE", "false").lower() == "true":
        print("AUTOMERGE=true set, but reviews still require a human merge.")


# ---------------------------------------------------------------- promote --

def promote(review_file: str | Path, merge_sha: str) -> dict:
    review_id, manifest_hash = parse_manifest(Path(review_file).read_text())
    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", merge_sha or ""):
        raise ValueError("a valid merge commit SHA is required")
    result = _rest("POST", "rpc/btw_promote_review", json={
        "p_review_id": review_id,
        "p_manifest_hash": manifest_hash,
        "p_merge_commit_sha": merge_sha.lower(),
    }).json()
    if not isinstance(result, dict):
        raise RuntimeError(f"promotion RPC returned an unexpected payload: {result!r}")
    print(json.dumps(result, sort_keys=True))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--promote", action="store_true")
    parser.add_argument("--review-file")
    parser.add_argument("--merge-sha")
    args = parser.parse_args()
    if args.open:
        open_pr()
    elif args.promote:
        if not args.review_file or not args.merge_sha:
            parser.error("--promote requires --review-file and --merge-sha")
        promote(args.review_file, args.merge_sha)
    else:
        parser.error("need --open or --promote")


if __name__ == "__main__":
    main()
