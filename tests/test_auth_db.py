import pytest

from tests.conftest import requires_db

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient   # noqa: E402


def _client(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("FRONTEND_URL", "https://frontend.test")
    from api import main
    return TestClient(main.app)


@requires_db
def test_register_login_me_and_profile(monkeypatch):
    import uuid
    uname = "user-" + uuid.uuid4().hex[:8]
    with _client(monkeypatch) as c:
        reg = c.post("/api/v1/auth/register",
                     json={"username": uname, "password": "s3cret123", "full_name": "T"})
        assert reg.status_code == 201
        body = reg.json()
        token = body["access_token"]
        assert body["user"]["profile_completed"] is False

        assert c.post("/api/v1/auth/register",
                      json={"username": uname, "password": "s3cret123"}).status_code == 409

        assert c.post("/api/v1/auth/login", json={"username": uname, "password": "s3cret123"}).status_code == 200
        assert c.post("/api/v1/auth/login", json={"username": uname, "password": "nope"}).status_code == 401

        assert c.get("/api/v1/users/me").status_code == 401

        auth = {"Authorization": f"Bearer {token}"}
        me = c.get("/api/v1/users/me", headers=auth).json()
        assert me["username"] == uname

        patched = c.patch("/api/v1/users/me", headers=auth, json={
            "ev_model_id": "hyundai-ioniq-5", "main_connector_type": "CCS2",
            "location_consent": True}).json()
        assert patched["profile_completed"] is True
        assert patched["main_connector_type"] == "CCS2"


@requires_db
def test_register_short_password_422(monkeypatch):
    with _client(monkeypatch) as c:
        assert c.post("/api/v1/auth/register",
                      json={"username": "x", "password": "short"}).status_code == 422


@requires_db
def test_google_callback_creates_user(monkeypatch):
    from api import google_oauth, security
    import uuid
    sub = "google-" + uuid.uuid4().hex[:8]
    monkeypatch.setattr(google_oauth, "exchange_code",
                        lambda code: {"sub": sub, "email": "g@x.com", "name": "G"})
    with _client(monkeypatch) as c:
        state = security.sign_state()
        r = c.get(f"/api/v1/auth/google/callback?code=abc&state={state}",
                  follow_redirects=False)
        assert r.status_code in (302, 307)
        assert r.headers["location"].startswith("https://frontend.test/auth/callback#token=")
        assert c.get("/api/v1/auth/google/callback?code=abc&state=bad",
                     follow_redirects=False).status_code == 400
