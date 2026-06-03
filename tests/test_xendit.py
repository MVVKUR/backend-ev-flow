import pytest

pytest.importorskip("httpx")

from api import xendit


class _Resp:
    status_code = 200
    text = ""
    def json(self):
        return {"id": "inv_1", "invoice_url": "https://checkout/inv_1", "status": "PENDING"}


@pytest.mark.unit
def test_create_invoice_builds_request_and_parses(monkeypatch):
    monkeypatch.setenv("XENDIT_SECRET_KEY", "sk_test")
    monkeypatch.setenv("XENDIT_BASE_URL", "https://api.xendit.co")
    captured = {}
    monkeypatch.setattr(xendit.httpx, "post",
                        lambda url, **kw: (captured.update(url=url, **kw) or _Resp()))
    out = xendit.create_invoice("ext-1", 50000, "Top up")
    assert out == {"id": "inv_1", "invoice_url": "https://checkout/inv_1", "status": "PENDING"}
    assert captured["url"] == "https://api.xendit.co/v2/invoices"
    assert captured["auth"] == ("sk_test", "")
    assert captured["json"] == {"external_id": "ext-1", "amount": 50000,
                                "description": "Top up", "currency": "IDR"}


@pytest.mark.unit
def test_create_invoice_requires_key(monkeypatch):
    monkeypatch.delenv("XENDIT_SECRET_KEY", raising=False)
    with pytest.raises(xendit.XenditError):
        xendit.create_invoice("e", 10000, "x")


@pytest.mark.unit
def test_create_invoice_raises_on_error_status(monkeypatch):
    monkeypatch.setenv("XENDIT_SECRET_KEY", "sk_test")
    class _Err:
        status_code = 400
        text = "bad"
        def json(self): return {}
    monkeypatch.setattr(xendit.httpx, "post", lambda url, **kw: _Err())
    with pytest.raises(xendit.XenditError):
        xendit.create_invoice("e", 10000, "x")
