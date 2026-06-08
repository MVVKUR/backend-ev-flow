import pytest

pytest.importorskip("httpx")

from api import google_oauth


@pytest.mark.unit
def test_build_auth_url(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://cb")
    url = google_oauth.build_auth_url("st8")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=cid" in url
    assert "state=st8" in url
    assert "scope=openid" in url


@pytest.mark.unit
def test_exchange_code(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "sec")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://cb")

    class _Tok:
        status_code = 200
        text = ""
        def json(self): return {"access_token": "at"}

    class _Info:
        status_code = 200
        text = ""
        def json(self): return {"sub": "g-1", "email": "a@b.com", "name": "A"}

    monkeypatch.setattr(google_oauth.httpx, "post", lambda url, **kw: _Tok())
    monkeypatch.setattr(google_oauth.httpx, "get", lambda url, **kw: _Info())
    assert google_oauth.exchange_code("code123") == {"sub": "g-1", "email": "a@b.com", "name": "A"}
