"""Thin client over the Xendit Invoice API (server-side), configured from env.

Creates hosted invoices for wallet top-ups. The secret key authenticates via HTTP
Basic (key as username, empty password). Tests monkeypatch create_invoice so the
suite makes no network calls.
"""
from __future__ import annotations

import os

import httpx


class XenditError(RuntimeError):
    """Xendit returned a non-2xx response or was unreachable."""


def _config() -> tuple[str, str, float]:
    return (
        os.getenv("XENDIT_BASE_URL", "https://api.xendit.co"),
        os.getenv("XENDIT_SECRET_KEY", ""),
        float(os.getenv("XENDIT_TIMEOUT_SECONDS", "30")),
    )


def create_invoice(external_id: str, amount_idr: int, description: str) -> dict:
    """Create a Xendit invoice. Returns {id, invoice_url, status}."""
    base, key, timeout = _config()
    if not key:
        raise XenditError("XENDIT_SECRET_KEY is not set")
    try:
        resp = httpx.post(
            f"{base}/v2/invoices",
            auth=(key, ""),
            json={"external_id": external_id, "amount": amount_idr,
                  "description": description, "currency": "IDR"},
            timeout=timeout,
        )
    except httpx.HTTPError as e:
        raise XenditError(f"Xendit request failed: {e}") from e
    if resp.status_code >= 300:
        raise XenditError(f"Xendit {resp.status_code}: {resp.text}")
    data = resp.json()
    return {"id": data["id"], "invoice_url": data["invoice_url"], "status": data["status"]}
