"""Google OAuth 2.0 Authorization Code flow (server side). Config read at call time."""
from __future__ import annotations

import os
from urllib.parse import urlencode

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


class GoogleOAuthError(RuntimeError):
    """Google returned a non-2xx response or was unreachable."""


def _config() -> tuple[str, str, str]:
    return (os.getenv("GOOGLE_CLIENT_ID", ""),
            os.getenv("GOOGLE_CLIENT_SECRET", ""),
            os.getenv("GOOGLE_REDIRECT_URI", ""))


def build_auth_url(state: str) -> str:
    client_id, _, redirect = _config()
    q = urlencode({"client_id": client_id, "redirect_uri": redirect, "response_type": "code",
                   "scope": "openid email profile", "state": state,
                   "access_type": "online", "prompt": "select_account"})
    return f"{AUTH_URL}?{q}"


def exchange_code(code: str) -> dict:
    """Exchange an auth code for the user's {sub, email, name}."""
    client_id, client_secret, redirect = _config()
    try:
        tok = httpx.post(TOKEN_URL, data={
            "client_id": client_id, "client_secret": client_secret, "code": code,
            "redirect_uri": redirect, "grant_type": "authorization_code"}, timeout=30)
        if tok.status_code >= 300:
            raise GoogleOAuthError(f"token {tok.status_code}: {tok.text}")
        access = tok.json()["access_token"]
        info = httpx.get(USERINFO_URL, headers={"Authorization": f"Bearer {access}"}, timeout=30)
        if info.status_code >= 300:
            raise GoogleOAuthError(f"userinfo {info.status_code}: {info.text}")
    except httpx.HTTPError as e:
        raise GoogleOAuthError(f"google request failed: {e}") from e
    d = info.json()
    return {"sub": d["sub"], "email": d.get("email"), "name": d.get("name")}
