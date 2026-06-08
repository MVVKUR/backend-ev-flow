"""Users persistence. SQL via the shared engine (same style as stations_repo/wallet_repo)."""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import text

from .db import engine

_COLS = ("id, username, password_hash, google_sub, email, full_name, account_type, "
         "ev_model_id, main_connector_type, location_consent, location_consent_at, "
         "profile_completed, created_at")


def _row(r) -> Optional[dict]:
    if r is None:
        return None
    d = dict(r)
    d["id"] = str(d["id"])  # psycopg returns uuid as UUID; callers/models expect str
    return d


def create_user(*, username=None, password_hash=None, google_sub=None, email=None,
                full_name=None, account_type="ev_user", ev_model_id=None,
                main_connector_type=None, location_consent=False,
                profile_completed=False) -> dict:
    user_id = str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(text("""
            INSERT INTO users
              (id, username, password_hash, google_sub, email, full_name, account_type,
               ev_model_id, main_connector_type, location_consent, location_consent_at,
               profile_completed)
            VALUES
              (:id, :username, :ph, :gs, :email, :fn, :at, :emid, :mct, :lc,
               CASE WHEN :lc THEN now() ELSE NULL END, :pc)
        """), {"id": user_id, "username": username, "ph": password_hash, "gs": google_sub,
               "email": email, "fn": full_name, "at": account_type, "emid": ev_model_id,
               "mct": main_connector_type, "lc": location_consent, "pc": profile_completed})
    return get_by_id(user_id)


def get_by_id(user_id: str) -> Optional[dict]:
    with engine.connect() as c:
        return _row(c.execute(text(f"SELECT {_COLS} FROM users WHERE id = :id"),
                              {"id": user_id}).mappings().first())


def get_by_username(username: str) -> Optional[dict]:
    with engine.connect() as c:
        return _row(c.execute(text(f"SELECT {_COLS} FROM users WHERE username = :u"),
                              {"u": username}).mappings().first())


def get_by_google_sub(sub: str) -> Optional[dict]:
    with engine.connect() as c:
        return _row(c.execute(text(f"SELECT {_COLS} FROM users WHERE google_sub = :s"),
                              {"s": sub}).mappings().first())


def update_profile(user_id: str, fields: dict, profile_completed: bool) -> dict:
    sets, params = [], {"id": user_id, "pc": profile_completed}
    for col in ("username", "ev_model_id", "main_connector_type", "location_consent"):
        if col in fields:
            sets.append(f"{col} = :{col}")
            params[col] = fields[col]
    if fields.get("location_consent") is True:
        sets.append("location_consent_at = now()")
    sets.append("profile_completed = :pc")
    with engine.begin() as c:
        c.execute(text(f"UPDATE users SET {', '.join(sets)} WHERE id = :id"), params)
    return get_by_id(user_id)
