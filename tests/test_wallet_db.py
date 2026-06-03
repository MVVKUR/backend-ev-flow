import pytest

from tests.conftest import requires_db

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient   # noqa: E402


@requires_db
def test_topup_creates_pending_and_webhook_credits(monkeypatch):
    from api import main, xendit
    # mock Xendit so no network; invoice id derives from external_id so it is retrievable
    monkeypatch.setattr(xendit, "create_invoice",
                        lambda ext, amt, desc: {"id": f"inv-{ext}",
                                                "invoice_url": f"https://checkout/{ext}",
                                                "status": "PENDING"})
    monkeypatch.setenv("XENDIT_CALLBACK_TOKEN", "tok123")
    with TestClient(main.app) as c:
        before = c.get("/api/v1/wallet").json()["balance_idr"]

        created = c.post("/api/v1/wallet/topup", json={"amount_idr": 50000}).json()
        assert created["status"] == "pending"
        assert created["invoice_url"].startswith("https://checkout/")

        inv_id = c.get("/api/v1/wallet/topups").json()[0]["xendit_invoice_id"]
        ok = c.post("/api/v1/webhooks/xendit", json={"id": inv_id, "status": "PAID"},
                    headers={"x-callback-token": "tok123"})
        assert ok.status_code == 200
        assert c.get("/api/v1/wallet").json()["balance_idr"] == before + 50000

        # idempotent: duplicate delivery does not double-credit
        c.post("/api/v1/webhooks/xendit", json={"id": inv_id, "status": "PAID"},
               headers={"x-callback-token": "tok123"})
        assert c.get("/api/v1/wallet").json()["balance_idr"] == before + 50000


@requires_db
def test_webhook_rejects_bad_token_and_ignores_unknown(monkeypatch):
    from api import main
    monkeypatch.setenv("XENDIT_CALLBACK_TOKEN", "tok123")
    with TestClient(main.app) as c:
        bad = c.post("/api/v1/webhooks/xendit", json={"id": "x", "status": "PAID"},
                     headers={"x-callback-token": "wrong"})
        assert bad.status_code == 401
        unknown = c.post("/api/v1/webhooks/xendit", json={"id": "inv-unknown", "status": "PAID"},
                         headers={"x-callback-token": "tok123"})
        assert unknown.status_code == 200


@requires_db
def test_topup_amount_below_min_is_422(monkeypatch):
    from api import main, xendit
    monkeypatch.setattr(xendit, "create_invoice", lambda *a, **k: {"id": "x", "invoice_url": "u", "status": "PENDING"})
    with TestClient(main.app) as c:
        assert c.post("/api/v1/wallet/topup", json={"amount_idr": 5000}).status_code == 422
