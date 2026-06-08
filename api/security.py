"""Password hashing, JWT, Google CSRF state, and the current_user dependency.

Env is read at call time so tests can monkeypatch it without reimporting.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Optional

import bcrypt
import jwt
from fastapi import Header, HTTPException


def _secret() -> str:
    return os.getenv("JWT_SECRET", "")


def _expire_minutes() -> int:
    return int(os.getenv("JWT_EXPIRE_MINUTES", "10080"))


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: str) -> str:
    payload = {"sub": str(user_id), "exp": int(time.time()) + _expire_minutes() * 60}
    return jwt.encode(payload, _secret(), algorithm="HS256")


def decode_access_token(token: str) -> str:
    return jwt.decode(token, _secret(), algorithms=["HS256"])["sub"]


def sign_state() -> str:
    secret = _secret()
    if not secret:
        raise RuntimeError("JWT_SECRET is not set")
    nonce = secrets.token_urlsafe(16)
    sig = hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    return f"{nonce}.{sig}"


def verify_state(state: Optional[str]) -> bool:
    secret = _secret()
    if not secret:  # fail closed: an empty key would make state trivially forgeable
        return False
    try:
        nonce, sig = (state or "").split(".", 1)
    except ValueError:
        return False
    expected = hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        user_id = decode_access_token(authorization.split(" ", 1)[1])
    except Exception:
        raise HTTPException(401, "invalid or expired token")
    from . import users_repo
    user = users_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(401, "user not found")
    return user
