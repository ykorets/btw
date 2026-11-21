"""Pending-review discovery shared by normalize and review."""

from btw_engine.review_state import load_pending_review_targets


class Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def test_load_pending_review_targets_uses_exact_manifest_ids():
    calls = []

    def rest(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == "review":
            return Response([{
                "id": "review-b", "batch_date": "2026-07-14",
                "pr_url": "https://github.com/ykorets/btw/pull/59",
            }, {
                "id": "review-a", "batch_date": "2026-07-13",
                "pr_url": None,
            }])
        return Response([{
            "review_id": "review-b", "unit_id": None,
            "permit_id": "permit-1", "event_id": None,
        }, {
            "review_id": "review-a", "unit_id": "unit-1",
            "permit_id": None, "event_id": None,
        }])

    pending = load_pending_review_targets(rest)

    assert pending[("permit", "permit-1")].pr_url.endswith("/59")
    assert pending[("unit", "unit-1")].batch_date == "2026-07-13"
    assert calls[1][2]["params"]["review_id"] == "in.(review-a,review-b)"


def test_load_pending_review_targets_avoids_empty_in_filter():
    calls = []

    def rest(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return Response([])

    assert load_pending_review_targets(rest) == {}
    assert len(calls) == 1 and calls[0][1] == "review"
