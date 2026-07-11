"""btw_engine.fetch — download → sha256 → R2 archive → document row.

Architecture §4.3: the archive is immutable, deduped by content hash, and is
the only read path downstream. Idempotent: same bytes → same key → skip.

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, R2_ACCOUNT_ID, R2_ACCESS_KEY, R2_SECRET.
Usage:
  python -m btw_engine.fetch --url URL --source manual-genesis --genre permit
  python -m btw_engine.fetch --backfill engine/backfill/genesis_docs.yaml
"""

import argparse
import hashlib
import os
import sys

import boto3
import httpx
import yaml

BUCKET = "btw-docs"
UA = {"User-Agent": "btw-engine/0.1 (+https://behindthewatt.com; public-records archive)"}

EXT = {
    "application/pdf": "pdf",
    "text/html": "html",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-excel": "xls",
    "text/csv": "csv",
}


def s3():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY"],
        aws_secret_access_key=os.environ["R2_SECRET"],
        region_name="auto",
    )


def upsert_document(row: dict) -> None:
    base = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/document"
    key = os.environ["SUPABASE_SERVICE_KEY"]
    r = httpx.post(
        base,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Prefer": "resolution=merge-duplicates",
        },
        params={"on_conflict": "sha256"},
        json=[row],
        timeout=30,
    )
    r.raise_for_status()


def fetch_one(client, url: str, source_id: str, genre: str) -> tuple[str, str]:
    r = httpx.get(url, headers=UA, follow_redirects=True, timeout=180)
    r.raise_for_status()
    data = r.content
    if len(data) < 1024:
        raise RuntimeError(f"suspiciously small response ({len(data)} bytes) — "
                           f"probably an error page, refusing to archive: {url}")
    sha = hashlib.sha256(data).hexdigest()
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    ext = EXT.get(ctype, "pdf" if data[:5] == b"%PDF-" else "bin")
    key = f"docs/{sha}.{ext}"

    existing = client.list_objects_v2(Bucket=BUCKET, Prefix=key).get("KeyCount", 0)
    if not existing:
        client.put_object(Bucket=BUCKET, Key=key, Body=data,
                          ContentType=ctype or "application/octet-stream",
                          Metadata={"source-url": url[:1024]})
    upsert_document({
        "source_id": source_id,
        "url": url,
        "r2_key": key,
        "sha256": sha,
        "doc_genre": genre,
    })
    return sha, ("cached" if existing else f"{len(data)//1024} KB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--source", default="manual-genesis")
    ap.add_argument("--genre", default="permit")
    ap.add_argument("--backfill", help="YAML list of {id,url,genre,source}")
    args = ap.parse_args()

    client = s3()
    jobs = []
    if args.backfill:
        for item in yaml.safe_load(open(args.backfill)):
            if item.get("status") == "pending":
                print(f"SKIP {item['id']}: marked pending ({item.get('note','')})")
                continue
            jobs.append((item["url"], item.get("source", "manual-genesis"),
                         item.get("genre", "permit"), item["id"]))
    elif args.url:
        jobs.append((args.url, args.source, args.genre, args.url[:60]))
    else:
        ap.error("need --url or --backfill")

    failures = 0
    for url, source, genre, label in jobs:
        try:
            sha, note = fetch_one(client, url, source, genre)
            print(f"OK   {label}: {sha[:12]} ({note})")
        except Exception as e:  # noqa: BLE001 — batch must continue
            failures += 1
            print(f"FAIL {label}: {e}")
    if failures:
        sys.exit(f"{failures} of {len(jobs)} fetches failed")


if __name__ == "__main__":
    main()
