"""Wallet + top-up persistence for the single global demo wallet (id=1)."""
from __future__ import annotations

import uuid

from sqlalchemy import text

from .db import engine


def get_wallet() -> dict:
    with engine.connect() as c:
        r = c.execute(text("SELECT balance_idr, updated_at FROM wallet WHERE id = 1")).mappings().first()
    return {"balance_idr": int(r["balance_idr"]), "updated_at": r["updated_at"]}


def create_topup(amount_idr: int, external_id: str, invoice_id: str, invoice_url: str) -> dict:
    topup_id = str(uuid.uuid4())
    with engine.begin() as c:
        c.execute(text("""
            INSERT INTO topups (id, external_id, xendit_invoice_id, amount_idr, status, invoice_url)
            VALUES (:id, :ext, :inv, :amt, 'pending', :url)
        """), {"id": topup_id, "ext": external_id, "inv": invoice_id, "amt": amount_idr, "url": invoice_url})
    return {"topup_id": topup_id, "amount_idr": amount_idr, "status": "pending", "invoice_url": invoice_url}


def mark_paid_and_credit(invoice_id: str) -> bool:
    """Flip a pending topup to paid and credit the wallet, atomically. Idempotent.

    Returns True if it credited, False if no pending topup matched (already paid or unknown).
    """
    with engine.begin() as c:
        row = c.execute(text("""
            UPDATE topups SET status = 'paid', paid_at = now()
            WHERE xendit_invoice_id = :inv AND status = 'pending'
            RETURNING amount_idr
        """), {"inv": invoice_id}).first()
        if row is None:
            return False
        c.execute(text("UPDATE wallet SET balance_idr = balance_idr + :amt, updated_at = now() WHERE id = 1"),
                  {"amt": int(row[0])})
    return True


def list_topups(limit: int = 20) -> list[dict]:
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT id, external_id, xendit_invoice_id, amount_idr, status, invoice_url, created_at, paid_at
            FROM topups ORDER BY created_at DESC LIMIT :lim
        """), {"lim": limit}).mappings().all()
    return [{**dict(r), "id": str(r["id"])} for r in rows]
