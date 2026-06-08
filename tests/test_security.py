import pytest

pytest.importorskip("bcrypt")
pytest.importorskip("jwt")

from api import security


@pytest.mark.unit
def test_password_hash_and_verify():
    h = security.hash_password("secret123")
    assert h != "secret123"
    assert security.verify_password("secret123", h)
    assert not security.verify_password("wrong", h)


@pytest.mark.unit
def test_jwt_roundtrip(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    tok = security.create_access_token("user-123")
    assert security.decode_access_token(tok) == "user-123"


@pytest.mark.unit
def test_jwt_invalid_rejected(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    import jwt as pyjwt
    with pytest.raises(pyjwt.PyJWTError):
        security.decode_access_token("not.a.jwt")


@pytest.mark.unit
def test_state_sign_and_verify(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    s = security.sign_state()
    assert security.verify_state(s)
    assert not security.verify_state(s + "tamper")
    assert not security.verify_state("garbage")
