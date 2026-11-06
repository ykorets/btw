import importlib

import pytest


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("BTW_API_URL", "https://data-api.example.test/")
    monkeypatch.setenv("BTW_API_KEY", "jwt-test")
    monkeypatch.delenv("BTW_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("BTW_ACCESS_CLIENT_SECRET", raising=False)
    from btw_engine import db as module
    return importlib.reload(module)


def test_url_joins_paths(db):
    assert db.url() == "https://data-api.example.test"
    assert db.url("document") == "https://data-api.example.test/document"
    assert db.url("/rpc/btw_promote_review") == "https://data-api.example.test/rpc/btw_promote_review"


def test_headers_without_access_token(db):
    assert db.headers() == {"Authorization": "Bearer jwt-test"}


def test_headers_with_access_token_and_extra(db, monkeypatch):
    monkeypatch.setenv("BTW_ACCESS_CLIENT_ID", "id.access")
    monkeypatch.setenv("BTW_ACCESS_CLIENT_SECRET", "secret")
    h = db.headers({"Prefer": "count=exact"})
    assert h["Authorization"] == "Bearer jwt-test"
    assert h["CF-Access-Client-Id"] == "id.access"
    assert h["CF-Access-Client-Secret"] == "secret"
    assert h["Prefer"] == "count=exact"
    assert "apikey" not in h
