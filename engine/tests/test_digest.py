"""Deterministic BTW Weekly rendering and delivery receipts."""

import datetime as dt
import json

from btw_engine import digest


UTC = dt.timezone.utc


def edition_data():
    return {
        "reviews": [{
            "id": "review-1", "manifest_hash": "a" * 64,
            "manifest_counts": {"units": 1, "permits": 1, "events": 1},
        }],
        "units": [{
            "id": "unit-1", "unit_count": 5, "oem": "Solar",
            "model": "Titan 350", "total_mw": 190, "mw_each": 38,
            "basis": "permitted", "verification_state": "verified",
            "facility": {"slug": "abilene", "name": "Abilene"},
            "sources": ["https://example.test/permit.pdf"],
        }],
        "permits": [{
            "id": "permit-1", "authority": "TCEQ", "permit_no": "177263",
            "status": "issued", "filed_at": "2024-01-01",
            "issued_at": "2024-05-01", "verification_state": "verified",
            "facility": {"slug": "abilene", "name": "Abilene"},
            "sources": ["https://example.test/permit.pdf"],
        }],
        "events": [{
            "id": "event-1", "event_date": "2026-07-18",
            "event_type": "permit_filed", "headline": "A filing entered the record",
            "source_url": "https://example.test/filing",
            "facility": {"slug": "abilene", "name": "Abilene"},
        }],
        "facilities": [],
        "announcements": [{
            "id": "announcement-1", "name": "Reported Project",
            "state": "TX", "county": "Taylor", "operating_status": "Proposed",
            "reported_capacity_mw": 1000,
            "source_document": {"url": "https://example.test/inventory.xlsx"},
        }],
        "snapshot": {
            "operating_gw": 0.70,
            "baseline_operating_gw": 0.51,
            "operating_delta_mw": 190.0,
            "published_facilities": 3,
            "reported_projects": 74,
            "third_party_reported_gw": 90.0,
            "aggregate_computed_at": "2026-07-18T00:00:00Z",
        },
    }


def test_edition_is_deterministic_and_keeps_reported_pipeline_separate():
    start = dt.datetime(2026, 7, 14, 13, tzinfo=UTC)
    end = dt.datetime(2026, 7, 21, 13, tzinfo=UTC)
    data = edition_data()

    first = digest.build_edition("2026-07-21", start, end, data)
    second = digest.build_edition("2026-07-21", start, end, data)

    assert first == second
    assert first["mode"] == "review"
    assert first["window_start"] == "2026-07-14T13:00:00Z"
    assert first["window_end"] == "2026-07-21T13:00:00Z"
    assert first["content_sha256"] == digest.content_hash(
        first["subject"], first["html"], first["text"])
    assert "5× Solar Titan 350" in first["text"]
    assert "rose by 190 MW" in first["text"]
    assert "not BTW-verified operating capacity" in first["html"]
    assert "{{{RESEND_UNSUBSCRIBE_URL}}}" in first["html"]
    assert "{{{RESEND_UNSUBSCRIBE_URL}}}" in first["text"]
    assert "candidate" not in first["subject"].lower()


def test_quiet_week_still_produces_an_honest_edition():
    data = edition_data()
    for key in ("reviews", "units", "permits", "events", "facilities", "announcements"):
        data[key] = []
    data["snapshot"]["operating_delta_mw"] = 0
    start = dt.datetime(2026, 7, 14, 13, tzinfo=UTC)
    end = dt.datetime(2026, 7, 21, 13, tzinfo=UTC)

    edition = digest.build_edition("2026-07-21", start, end, data)

    assert edition["subject"].endswith("no new published record changes")
    assert "No sealed review manifest was promoted" in edition["text"]
    assert "No published third-party inventory updates" in edition["text"]


def test_archive_cursor_closes_gaps_and_ignores_current_issue(tmp_path):
    (tmp_path / "2026-07-07.json").write_text(json.dumps({
        "window": {"end": "2026-07-07T13:00:00Z"}
    }))
    (tmp_path / "2026-07-14.json").write_text(json.dumps({
        "window": {"end": "2026-07-14T13:00:00Z"}
    }))
    (tmp_path / "2026-07-21.json").write_text(json.dumps({
        "window": {"end": "2099-01-01T00:00:00Z"}
    }))
    default = dt.datetime(2026, 7, 14, 13, tzinfo=UTC)

    cursor = digest.previous_window_end(tmp_path, "2026-07-21", default)

    assert cursor == dt.datetime(2026, 7, 14, 13, tzinfo=UTC)


def test_receipt_contains_window_manifest_and_delivery_audit(tmp_path):
    start = dt.datetime(2026, 7, 14, 13, tzinfo=UTC)
    end = dt.datetime(2026, 7, 21, 13, tzinfo=UTC)
    data = edition_data()
    edition = digest.build_edition("2026-07-21", start, end, data)

    digest.write_receipt(
        tmp_path, "2026-07-21", start, end, data, edition,
        {"status": "send_requested", "broadcast_id": "broadcast-1"},
    )

    receipt = json.loads((tmp_path / "2026-07-21.json").read_text())
    assert receipt["window"] == {
        "start": "2026-07-14T13:00:00Z",
        "end": "2026-07-21T13:00:00Z",
    }
    assert receipt["review_manifests"][0]["manifest_hash"] == "a" * 64
    assert receipt["delivery"]["broadcast_id"] == "broadcast-1"
    assert (tmp_path / "2026-07-21.md").exists()
    assert (tmp_path / "2026-07-21.html").exists()


def test_tuesday_edition_freezes_for_review_on_monday():
    assert digest.review_window_end(dt.date(2026, 7, 21)) == dt.datetime(
        2026, 7, 20, 13, tzinfo=UTC
    )


def test_delivery_cursor_uses_only_worker_approved_window(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"window_end": "2026-07-20T13:00:00Z"}

    captured = {}

    def post(endpoint, **kwargs):
        captured["endpoint"] = endpoint
        captured.update(kwargs)
        return Response()

    monkeypatch.setenv("DIGEST_TRIGGER_SECRET", "test-secret")
    monkeypatch.setattr(digest.httpx, "post", post)

    cursor = digest.delivery_cursor("https://newsletter.example/broadcast")

    assert cursor == dt.datetime(2026, 7, 20, 13, tzinfo=UTC)
    assert captured["json"] == {"mode": "cursor"}
