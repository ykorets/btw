"""btw_engine.digest — BTW Weekly drafter (M8, digest_writer role).

Pulls the week's published events, fresh candidates and the current
aggregate, asks the digest_writer model for a draft in the house voice,
and opens a PR with digest/YYYY-MM-DD.md. A human edits and merges —
the machine drafts, it does not publish.

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, provider key for digest_writer,
GITHUB_TOKEN, GITHUB_REPOSITORY.
Usage: python -m btw_engine.digest
"""

import base64
import datetime as dt
import sys
import os

from btw_engine import llm
from btw_engine.review import _rest, _gh

VOICE = """You draft "BTW Weekly" for Behind the Watt (behindthewatt.com),
the open database of behind-the-meter data center power. Voice: calm,
precise, record-keeper's tone — a journal of the permit record, not a
newsletter of hype. Facts carry the piece; no exclamation points, no
"exciting". Cite what the data shows and say plainly what is unverified.
Structure: a 2-3 sentence lede on the week's most consequential change,
then short sections: "The record" (fact changes), "New in the funnel"
(notable candidates), "Watch next". End with the standing line:
"All data CC BY 4.0. Cite as: Behind the Watt, behindthewatt.com."
Output pure markdown, no preamble."""


def gather() -> dict:
    since = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    events = _rest("GET", "event", params={
        "select": "event_date,event_type,headline,facility(slug)",
        "fact_state": "eq.published",
        "event_date": f"gte.{since}",
        "order": "event_date.desc"}).json()
    cands = _rest("GET", "candidate", params={
        "select": "source_id,title,found_at",
        "found_at": f"gte.{since}T00:00:00Z",
        "order": "found_at.desc", "limit": "60"}).json()
    agg = _rest("GET", "aggregate", params={
        "select": "metric,value,computed_at",
        "metric": "eq.operating_gw",
        "order": "computed_at.desc", "limit": "2"}).json()
    return {"since": since, "events": events, "candidates": cands,
            "aggregate": agg}


def main() -> None:
    data = gather()
    if not data["events"] and not data["candidates"]:
        print("quiet week — no digest")
        return
    facts = []
    for a in data["aggregate"]:
        facts.append(f"operating_gw={a['value']} (computed {a['computed_at'][:10]})")
    for e in data["events"]:
        facts.append(f"EVENT {e['event_date']} [{e['event_type']}] "
                     f"{(e.get('facility') or {}).get('slug')}: {e['headline']}")
    for c in data["candidates"][:40]:
        facts.append(f"CANDIDATE [{c['source_id']}] {c['title']}")

    draft = llm.complete("digest_writer", [{
        "role": "user",
        "content": VOICE + "\n\nWEEK SINCE " + data["since"]
        + " — RAW RECORD:\n" + "\n".join(facts)}],
        purpose="digest")

    day = dt.date.today().isoformat()
    repo = os.environ["GITHUB_REPOSITORY"]
    branch = f"digest/{day}"
    main_sha = _gh("GET", f"/repos/{repo}/git/ref/heads/main"
                   ).json()["object"]["sha"]
    r = _gh("POST", f"/repos/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": main_sha})
    if r.status_code not in (201, 422):
        sys.exit(f"branch create failed: {r.status_code}")
    path = f"digest/{day}.md"
    prev = _gh("GET", f"/repos/{repo}/contents/{path}", params={"ref": branch})
    payload = {"message": f"digest draft: {day}",
               "content": base64.b64encode(draft.encode()).decode(),
               "branch": branch}
    if prev.status_code == 200:
        payload["sha"] = prev.json()["sha"]
    r = _gh("PUT", f"/repos/{repo}/contents/{path}", json=payload)
    if r.status_code not in (200, 201):
        sys.exit(f"file put failed: {r.status_code} {r.text[:150]}")
    r = _gh("POST", f"/repos/{repo}/pulls", json={
        "title": f"BTW Weekly draft — {day}",
        "head": branch, "base": "main",
        "body": "Machine draft in the house voice. Edit, then merge to "
                "accept into the archive. The machine drafts; it does not "
                "publish."})
    if r.status_code == 201:
        print("digest PR:", r.json()["html_url"])
    elif r.status_code == 422:
        print("digest PR already open")
    else:
        sys.exit(f"PR create failed: {r.status_code}")


if __name__ == "__main__":
    main()
