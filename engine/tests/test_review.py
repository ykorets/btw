"""Sealed manifest and single-transaction promotion tests."""

import os

import pytest

os.environ.setdefault("SUPABASE_URL", "https://example.test")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")

from btw_engine import review  # noqa: E402


REVIEW_ID = "11111111-1111-4111-8111-111111111111"
MANIFEST_HASH = "a" * 64


class Response:
    def __init__(self, payload=None):
        self.payload = payload

    def json(self):
        return self.payload


def test_manifest_metadata_round_trip_and_tamper_rejection():
    text = (f"<!-- BTW_REVIEW_ID: {REVIEW_ID} -->\n"
            f"<!-- BTW_MANIFEST_HASH: {MANIFEST_HASH} -->\n# Review\n")
    assert review.parse_manifest(text) == (REVIEW_ID, MANIFEST_HASH)
    with pytest.raises(ValueError, match="sealed manifest"):
        review.parse_manifest(text.replace(MANIFEST_HASH, "not-a-hash"))


def test_create_manifest_snapshots_exact_sorted_ids(monkeypatch):
    calls = []

    def fake_rest(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return Response([{"review_id": REVIEW_ID,
                          "manifest_hash": MANIFEST_HASH}])

    monkeypatch.setattr(review, "_rest", fake_rest)
    manifest = review.create_review_manifest(
        "2026-07-13",
        [{"staged": {"id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"}},
         {"staged": {"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}}],
        [{"staged": {"id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc"}}],
        [{"id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd"}],
    )

    assert manifest["review_id"] == REVIEW_ID
    assert calls == [("POST", "rpc/btw_create_review_manifest", {
        "json": {
            "p_batch_date": "2026-07-13",
            "p_unit_ids": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                           "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"],
            "p_permit_ids": ["cccccccc-cccc-4ccc-8ccc-cccccccccccc"],
            "p_event_ids": ["dddddddd-dddd-4ddd-8ddd-dddddddddddd"],
        },
    })]


def test_render_includes_manifest_permit_diff_and_receipt():
    permit = {
        "staged": {
            "id": "p2", "permit_no": "01156-01PC",
            "authority": "Shelby County Health Department",
            "permit_type": "Air construction permit", "status": "issued",
            "filed_at": None, "issued_at": "2025-07-02",
            "verification_state": "source_asserted",
            "facility": {"slug": "xai-colossus-1"},
        },
        "published": {
            "permit_no": "01156-01PC", "authority": "Shelby County",
            "permit_type": None, "status": "issued", "filed_at": None,
            "issued_at": None, "verification_state": None,
        },
        "provenance": [{
            "fact_field": "issued_at", "support_kind": "direct",
            "derivation": None,
            "claim": {
                "quote": "issued the permit on July 2, 2025",
                "match_score": 1.0,
                "document": {"url": "https://example.test/permit.pdf"},
            },
        }],
    }
    text = review.render([], [permit], [], "2026-07-13", {
        "review_id": REVIEW_ID, "manifest_hash": MANIFEST_HASH,
    })

    assert f"BTW_REVIEW_ID: {REVIEW_ID}" in text
    assert f"BTW_MANIFEST_HASH: {MANIFEST_HASH}" in text
    assert "permit 01156-01PC" in text
    assert "issued_at" in text and "2025-07-02" in text
    assert "issued the permit on July 2, 2025" in text


def test_promote_is_one_rpc_and_never_client_side_fact_patches(
        monkeypatch, tmp_path):
    review_file = tmp_path / "review.md"
    review_file.write_text(
        f"<!-- BTW_REVIEW_ID: {REVIEW_ID} -->\n"
        f"<!-- BTW_MANIFEST_HASH: {MANIFEST_HASH} -->\n")
    calls = []

    def fake_rest(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return Response({"status": "promoted", "units": 1, "permits": 1})

    monkeypatch.setattr(review, "_rest", fake_rest)
    result = review.promote(review_file, "b" * 40)

    assert result["status"] == "promoted"
    assert calls == [("POST", "rpc/btw_promote_review", {
        "json": {
            "p_review_id": REVIEW_ID,
            "p_manifest_hash": MANIFEST_HASH,
            "p_merge_commit_sha": "b" * 40,
        },
    })]
    assert not any(path in {"unit", "permit", "event"}
                   for _method, path, _kwargs in calls)


def test_incomplete_staging_is_held_out_of_manifest():
    permit = {
        "staged": {
            "id": "permit-staged", "facility_id": "facility-1",
            "authority": "TCEQ", "permit_no": "177263",
            "permit_type": None, "status": "issued", "filed_at": None,
            "issued_at": None, "verification_state": None,
            "facility": {"slug": "fixture"},
        },
        "published": None,
        "provenance": [],
    }
    good_event = {"id": "event-good", "headline": "Filed",
                  "source_url": "https://example.test/notice"}
    bad_event = {"id": "event-bad", "headline": "No receipt",
                 "source_url": None}

    units, permits, events, held = review.partition_review_ready(
        [], [permit], [good_event, bad_event])

    assert units == [] and permits == []
    assert events == [good_event]
    assert {(item["kind"], item["id"]) for item in held} == {
        ("permit", "permit-staged"), ("event", "event-bad")}
