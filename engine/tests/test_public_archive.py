import json

import pytest
from botocore.exceptions import ClientError

from btw_engine import public_archive
from btw_engine.public_archive import archive_url, load_manifest


SHA = "a" * 64


def test_manifest_requires_explicit_redistribution_basis(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"version": 1, "documents": {
        SHA: {"rights_basis": "unknown"}}}))
    with pytest.raises(ValueError, match="rights basis"):
        load_manifest(path)


def test_archive_url_is_fail_closed_without_approval_or_base():
    assert archive_url(SHA, f"docs/{SHA}.pdf", base_url="",
                       manifest={SHA: {"rights_basis": "public_record"}}) is None
    assert archive_url(SHA, f"docs/{SHA}.pdf",
                       base_url="https://evidence.example",
                       manifest={}) is None
    assert archive_url(SHA, f"docs/{SHA}.pdf",
                       base_url="https://evidence.example",
                       manifest={SHA: {"rights_basis": "public_record"}}) == \
        f"https://evidence.example/docs/{SHA}.pdf"


class MissingPublicBucketClient:
    public_lookups = 0

    def list_objects_v2(self, *, Bucket, Prefix):
        if Bucket == public_archive.BUCKET:
            return {"Contents": [{"Key": f"docs/{SHA}.pdf"}]}
        self.public_lookups += 1
        raise ClientError(
            {"Error": {"Code": "NoSuchBucket", "Message": "missing"}},
            "ListObjectsV2")


def test_dry_run_can_plan_before_public_bucket_exists(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"version": 1, "documents": {
        SHA: {"rights_basis": "public_record"}}}))
    client = MissingPublicBucketClient()
    monkeypatch.setattr(public_archive, "s3", lambda: client)
    assert public_archive.sync(dry_run=True, manifest_path=path) == 1
    assert client.public_lookups == 0


def test_write_fails_closed_when_public_bucket_is_missing(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"version": 1, "documents": {
        SHA: {"rights_basis": "public_record"}}}))
    monkeypatch.setattr(public_archive, "s3", lambda: MissingPublicBucketClient())
    with pytest.raises(RuntimeError, match="does not exist"):
        public_archive.sync(dry_run=False, manifest_path=path)
