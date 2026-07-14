"""Copy redistribution-approved evidence into the public R2 bucket.

The ingestion bucket remains private and contains every archived source. Only
SHA-256 objects explicitly approved in ``engine/public_evidence.json`` are
copied here. A custom domain can therefore expose the public bucket without
accidentally publishing restricted satellite or third-party material.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from botocore.exceptions import ClientError

from btw_engine.fetch import BUCKET, s3

PUBLIC_BUCKET = "btw-evidence-public"
DEFAULT_MANIFEST = Path(__file__).resolve().parents[2] / "public_evidence.json"


def load_manifest(path: str | Path = DEFAULT_MANIFEST) -> dict[str, dict]:
    payload = json.loads(Path(path).read_text())
    if payload.get("version") != 1 or not isinstance(payload.get("documents"), dict):
        raise ValueError("public evidence manifest must be version 1")
    for sha, metadata in payload["documents"].items():
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError(f"invalid evidence sha256: {sha}")
        if metadata.get("rights_basis") not in {"public_record", "licensed"}:
            raise ValueError(f"evidence {sha} has no redistribution rights basis")
    return payload["documents"]


def archive_url(sha256: str | None, r2_key: str | None,
                *, base_url: str | None = None,
                manifest: dict[str, dict] | None = None) -> str | None:
    base = (base_url if base_url is not None
            else os.environ.get("BTW_ARCHIVE_BASE_URL", "")).rstrip("/")
    approved = manifest if manifest is not None else load_manifest()
    if not base or not sha256 or not r2_key or sha256 not in approved:
        return None
    return f"{base}/{r2_key.lstrip('/')}"


def sync(*, dry_run: bool = False,
         manifest_path: str | Path = DEFAULT_MANIFEST) -> int:
    client = s3()
    approved = load_manifest(manifest_path)
    copied = 0
    for sha, metadata in sorted(approved.items()):
        matches = client.list_objects_v2(
            Bucket=BUCKET, Prefix=f"docs/{sha}.").get("Contents", [])
        if len(matches) != 1:
            raise RuntimeError(
                f"expected one private archive object for {sha}, got {len(matches)}")
        key = matches[0]["Key"]
        try:
            exists = client.list_objects_v2(
                Bucket=PUBLIC_BUCKET, Prefix=key).get("KeyCount", 0)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code not in {"NoSuchBucket", "404"}:
                raise
            if not dry_run:
                raise RuntimeError(
                    f"public archive bucket {PUBLIC_BUCKET!r} does not exist") \
                    from exc
            exists = 0
        if exists:
            print(f"KEEP {key}")
            continue
        print(f"{'WOULD COPY' if dry_run else 'COPY'} {key} "
              f"[{metadata['rights_basis']}]")
        if not dry_run:
            source = client.get_object(Bucket=BUCKET, Key=key)
            client.put_object(
                Bucket=PUBLIC_BUCKET,
                Key=key,
                Body=source["Body"],
                ContentType=source.get("ContentType") or "application/octet-stream",
                CacheControl="public, max-age=31536000, immutable",
                Metadata={
                    "sha256": sha,
                    "rights-basis": metadata["rights_basis"],
                    "source-bucket": BUCKET,
                },
            )
        copied += 1
    return copied


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    args = parser.parse_args()
    copied = sync(dry_run=args.dry_run, manifest_path=args.manifest)
    print(f"public evidence: {copied} object(s) "
          f"{'planned' if args.dry_run else 'copied'}")


if __name__ == "__main__":
    main()
