"""btw_engine.review — daily review PR + promote-on-merge (M5).

--open   staged-vs-published diff (quotes inline, from fact_provenance) plus
         fresh keyword-hit candidates → review/YYYY-MM-DD.md on branch
         review/YYYY-MM-DD → PR via GitHub REST. Nothing to review → exits 0
         quietly. Idempotent per day.
--promote  staging→published swap by natural key (facility_id, model),
         operating_gw aggregate recomputed and recorded. Runs from the
         review-promote workflow after a human merges the review PR.

AUTOMERGE env flag is honored as a gate only: v1 never auto-merges.

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY; for --open also GITHUB_TOKEN,
GITHUB_REPOSITORY (both provided by Actions).
"""

import argparse
import base64
import datetime as dt
import os
import sys

import httpx

GH_API = "https://api.github.com"


def _rest(method: str, path: str, **kw) -> httpx.Response:
    base = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/" + path
    key = os.environ["SUPABASE_SERVICE_KEY"]
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    headers.update(kw.pop("headers", {}))
    r = httpx.request(method, base, headers=headers, timeout=60, **kw)
    r.raise_for_status()
    return r


def _gh(method: str, path: str, **kw) -> httpx.Response:
    headers = {
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
    }
    return httpx.request(method, GH_API + path, headers=headers,
                         timeout=30, **kw)


# ------------------------------------------------------------------- diff --

def staged_units() -> list[dict]:
    staged = _rest("GET", "unit", params={
        "select": "id,facility_id,oem,model,unit_count,mw_each,"
                  "hours_permitted,facility(slug,name)",
        "fact_state": "eq.staging"}).json()
    out = []
    for s in staged:
        pub = _rest("GET", "unit", params={
            "select": "id,unit_count,mw_each,hours_permitted",
            "facility_id": f"eq.{s['facility_id']}",
            "model": f"eq.{s['model']}",
            "fact_state": "eq.published"}).json()
        prov = _rest("GET", "fact_provenance", params={
            "select": "fact_field,note,"
                      "claim:claim_id(quote,match_score,value,"
                      "document:document_id(url))",
            "fact_table": "eq.unit", "fact_id": f"eq.{s['id']}"}).json()
        out.append({"staged": s, "published": pub[0] if pub else None,
                    "provenance": prov})
    return out


def fresh_candidates(hours: int = 36) -> list[dict]:
    since = (dt.datetime.now(dt.timezone.utc)
             - dt.timedelta(hours=hours)).isoformat()
    return _rest("GET", "candidate", params={
        "select": "external_id,title,url,payload,source_id",
        "found_at": f"gt.{since}",
        "payload->>kw_hit": "eq.true",
        "order": "found_at.desc"}).json()


def render(units: list[dict], cands: list[dict], day: str) -> str:
    lines = [f"# Review — {day}", ""]
    if units:
        lines += ["## Staged fact updates", ""]
        for u in units:
            s, p = u["staged"], u["published"]
            fac = (s.get("facility") or {}).get("slug", "?")
            lines.append(f"### {fac} — {s['oem']} {s['model']}")
            lines.append("")
            for col in ("unit_count", "mw_each", "hours_permitted"):
                if p is not None and s.get(col) != p.get(col):
                    lines.append(f"- **{col}**: {p.get(col)} → **{s.get(col)}**")
            lines.append("")
            for pr in u["provenance"]:
                c = pr.get("claim") or {}
                doc = (c.get("document") or {}).get("url", "")
                q = (c.get("quote") or "").strip()
                lines.append(f"> {q}")
                lines.append(f"> — {pr['fact_field']}, match "
                             f"{c.get('match_score')}, [source]({doc})")
                lines.append("")
    if cands:
        lines += ["## New keyword candidates (last 36h)", ""]
        for c in cands:
            lines.append(f"- `{c['source_id']}` #{c['external_id']}: "
                         f"{c['title']}")
        lines.append("")
    lines += ["---",
              "Merging this PR promotes the staged updates to published and "
              "regenerates the mirror. Candidates are informational.", ""]
    return "\n".join(lines)


# ------------------------------------------------------------------- open --

def open_pr() -> None:
    day = dt.date.today().isoformat()
    units = staged_units()
    cands = fresh_candidates()
    if not units and not cands:
        print("nothing to review today")
        return
    body = render(units, cands, day)
    repo = os.environ["GITHUB_REPOSITORY"]
    branch = f"review/{day}"

    main_sha = _gh("GET", f"/repos/{repo}/git/ref/heads/main"
                   ).json()["object"]["sha"]
    r = _gh("POST", f"/repos/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": main_sha})
    if r.status_code not in (201, 422):  # 422 = branch exists
        sys.exit(f"branch create failed: {r.status_code} {r.text[:200]}")

    path = f"review/{day}.md"
    prev = _gh("GET", f"/repos/{repo}/contents/{path}", params={"ref": branch})
    payload = {
        "message": f"review: {day}",
        "content": base64.b64encode(body.encode()).decode(),
        "branch": branch,
    }
    if prev.status_code == 200:
        payload["sha"] = prev.json()["sha"]
    r = _gh("PUT", f"/repos/{repo}/contents/{path}", json=payload)
    if r.status_code not in (200, 201):
        sys.exit(f"file put failed: {r.status_code} {r.text[:200]}")

    r = _gh("POST", f"/repos/{repo}/pulls", json={
        "title": f"Review {day}: {len(units)} staged update(s), "
                 f"{len(cands)} new candidate(s)",
        "head": branch, "base": "main",
        "body": "Daily review batch. See the markdown diff; quotes are "
                "anchored to archived documents.\n\n"
                "**Merge = approve**: staged facts go live and the mirror "
                "regenerates.",
    })
    if r.status_code == 201:
        print("review PR opened:", r.json()["html_url"])
    elif r.status_code == 422:
        print("review PR already open for", branch)
    else:
        sys.exit(f"PR create failed: {r.status_code} {r.text[:200]}")

    if os.environ.get("AUTOMERGE", "false").lower() == "true":
        print("AUTOMERGE=true set, but v1 never auto-merges (flag-gated).")


# ---------------------------------------------------------------- promote --

def promote() -> None:
    staged = _rest("GET", "unit", params={
        "select": "id,facility_id,model",
        "fact_state": "eq.staging"}).json()
    if not staged:
        print("nothing staged to promote")
        return
    for s in staged:
        pub = _rest("GET", "unit", params={
            "select": "id",
            "facility_id": f"eq.{s['facility_id']}",
            "model": f"eq.{s['model']}",
            "fact_state": "eq.published"}).json()
        for p in pub:
            _rest("DELETE", "unit", params={"id": f"eq.{p['id']}"})
        _rest("PATCH", "unit", params={"id": f"eq.{s['id']}"},
              json={"fact_state": "published"})
        print(f"promoted unit {s['model']} ({s['id'][:8]})")

    ops = _rest("GET", "facility", params={
        "select": "status,unit(unit_count,mw_each,fact_state)",
        "fact_state": "eq.published", "status": "eq.operating"}).json()
    gw = round(sum(
        (u.get("unit_count") or 0) * float(u.get("mw_each") or 0)
        for f in ops for u in f.get("unit", [])
        if u.get("fact_state") == "published") / 1000, 2)
    _rest("POST", "aggregate", json=[{
        "metric": "operating_gw", "value": gw,
        "method": "sum(unit_count*mw_each) over published operating "
                  "facilities; recomputed at promote",
        "inputs_note": f"promote of {len(staged)} staged unit row(s)"}])
    print(f"aggregate operating_gw refreshed: {gw}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--promote", action="store_true")
    args = ap.parse_args()
    if args.open:
        open_pr()
    elif args.promote:
        promote()
    else:
        ap.error("need --open or --promote")


if __name__ == "__main__":
    main()
