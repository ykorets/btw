import sys

from btw_engine import hotfix


class Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def test_hotfix_preserves_source_and_reconciles_retry(monkeypatch):
    rows = {}
    opened = []

    def rest(method, table, **kwargs):
        if table == "facility":
            return Response([{"id": "facility-1"}])
        if method == "GET":
            row = rows.get(kwargs["params"]["id"].removeprefix("eq."))
            return Response([row] if row else [])
        row = kwargs["json"][0]
        rows[row["id"]] = row
        return Response([row])

    monkeypatch.setattr(hotfix, "_rest", rest)
    monkeypatch.setattr(hotfix, "open_pr", lambda: opened.append(True))
    monkeypatch.setattr(sys, "argv", ["hotfix", "--facility", "test", "--date", "2026-10-19",
        "--type", "court_order", "--headline", "Court declines dismissal", "--url", "https://example.org/order.pdf"])
    hotfix.main()
    hotfix.main()
    assert len(rows) == 1
    row = next(iter(rows.values()))
    assert row["source_url"] == "https://example.org/order.pdf"
    assert row["headline"] == "Court declines dismissal"
    assert row["fact_state"] == "staging"
    assert len(opened) == 2
