# Xendit Wallet Top-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single global demo wallet that users top up through Xendit's hosted Invoice (sandbox), credited by a verified webhook.

**Architecture:** A thin `httpx` Xendit client creates an invoice; a `topups` row tracks it as `pending`; Xendit's webhook (verified by `x-callback-token`) atomically flips the topup to `paid` and credits the singleton `wallet` row. New tables live in the existing Postgres; tests mock the Xendit client (no network).

**Tech Stack:** FastAPI, SQLAlchemy 2 (psycopg), Alembic, httpx, PostgreSQL, pytest.

**Spec:** `docs/superpowers/specs/2026-06-03-xendit-wallet-topup-design.md`

---

## File structure

| File | Responsibility | New/Modify |
|---|---|---|
| `requirements-api.txt` | add `httpx` (runtime) | Modify |
| `api/xendit.py` | Xendit Invoice client (httpx, env config) | Create |
| `alembic/versions/0002_wallet_topups.py` | wallet + topups tables | Create |
| `api/wallet_repo.py` | wallet + topup DB operations | Create |
| `api/models.py` | TopupRequest, TopupCreated, WalletBalance, Topup | Modify |
| `api/main.py` | topup / wallet / webhook / topups endpoints | Modify |
| `tests/test_xendit.py` | client unit tests (httpx mocked) | Create |
| `tests/test_wallet_db.py` | DB-gated integration | Create |
| `.env.example`, `.env.deploy.example`, `podman-compose.yml` | XENDIT_* config | Modify |

---

## Task 1: httpx dep + Xendit client

**Files:** Modify `requirements-api.txt`; Create `api/xendit.py`; Create `tests/test_xendit.py`.

- [ ] **Step 1: Add httpx to `requirements-api.txt`** (append):
```
httpx>=0.27
```
Run: `.venv/bin/pip install "httpx>=0.27"` (likely already present from the test stack; confirm no error).

- [ ] **Step 2: Write `api/xendit.py`** (config read at call time, so no module reload in tests):
```python
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
```

- [ ] **Step 3: Write the failing tests** `tests/test_xendit.py`:
```python
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
```

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_xendit.py -v` → expect 3 passed.

- [ ] **Step 5: Commit**
```bash
git add requirements-api.txt api/xendit.py tests/test_xendit.py
git commit -m "feat: Xendit invoice client (env-configured, mockable)"
```

---

## Task 2: Alembic migration (wallet + topups)

**Files:** Create `alembic/versions/0002_wallet_topups.py`.

- [ ] **Step 1: Write the migration:**
```python
"""wallet + topups

Revision ID: 0002_wallet_topups
Revises: 0001_stations
Create Date: 2026-06-03
"""
from alembic import op

revision = "0002_wallet_topups"
down_revision = "0001_stations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE wallet (
            id          smallint PRIMARY KEY,
            balance_idr bigint NOT NULL DEFAULT 0 CHECK (balance_idr >= 0),
            updated_at  timestamptz NOT NULL DEFAULT now()
        );
    """)
    op.execute("INSERT INTO wallet (id, balance_idr) VALUES (1, 0);")
    op.execute("""
        CREATE TABLE topups (
            id                uuid PRIMARY KEY,
            external_id       text NOT NULL UNIQUE,
            xendit_invoice_id text UNIQUE,
            amount_idr        bigint NOT NULL CHECK (amount_idr > 0),
            status            text NOT NULL DEFAULT 'pending',
            invoice_url       text,
            created_at        timestamptz NOT NULL DEFAULT now(),
            paid_at           timestamptz
        );
    """)
    op.execute("CREATE INDEX topups_invoice_ix ON topups (xendit_invoice_id);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS topups;")
    op.execute("DROP TABLE IF EXISTS wallet;")
```

- [ ] **Step 2: Sanity** `.venv/bin/python -c "import ast; ast.parse(open('alembic/versions/0002_wallet_topups.py').read()); print('ok')"` and `.venv/bin/alembic history` (shows `0001_stations -> 0002_wallet_topups (head)`).

- [ ] **Step 3: Commit**
```bash
git add alembic/versions/0002_wallet_topups.py
git commit -m "feat: migration for wallet + topups tables"
```

---

## Task 3: Wallet repository

**Files:** Create `api/wallet_repo.py`.

- [ ] **Step 1: Write `api/wallet_repo.py`:**
```python
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
    # psycopg returns the uuid `id` as a UUID object; the Topup model field is str and
    # Pydantic v2 will not coerce UUID -> str, so stringify it here.
    return [{**dict(r), "id": str(r["id"])} for r in rows]
```

- [ ] **Step 2: Compile + import** `.venv/bin/python -m py_compile api/wallet_repo.py && .venv/bin/python -c "import api.wallet_repo; print('ok')"` (engine is lazy; no DB connection on import).

- [ ] **Step 3: Commit**
```bash
git add api/wallet_repo.py
git commit -m "feat: wallet repository (balance, create topup, paid+credit, history)"
```

---

## Task 4: Pydantic models

**Files:** Modify `api/models.py`.

- [ ] **Step 1:** Add at the end of `api/models.py` (the file already has `from datetime import datetime`? if not, add it to the imports at the top):
```python
from datetime import datetime  # add to the top imports if not already present


class TopupRequest(BaseModel):
    amount_idr: int = Field(..., ge=10000, description="Top-up amount in IDR (Xendit min 10000).", examples=[50000])


class TopupCreated(BaseModel):
    topup_id: str
    amount_idr: int
    status: str = Field(..., examples=["pending"])
    invoice_url: str = Field(..., description="Open this hosted Xendit page to pay.")


class WalletBalance(BaseModel):
    balance_idr: int = Field(..., examples=[200000])
    currency: str = Field("IDR", examples=["IDR"])
    updated_at: datetime


class Topup(BaseModel):
    id: str
    external_id: str
    xendit_invoice_id: Optional[str] = None
    amount_idr: int
    status: str = Field(..., examples=["paid"])
    invoice_url: Optional[str] = None
    created_at: datetime
    paid_at: Optional[datetime] = None
```
(Put the `from datetime import datetime` import with the other top-of-file imports, not mid-file.)

- [ ] **Step 2:** Verify `.venv/bin/python -c "from api.models import TopupRequest, TopupCreated, WalletBalance, Topup; print('ok')"`.

- [ ] **Step 3: Commit**
```bash
git add api/models.py
git commit -m "feat: wallet/topup pydantic models"
```

---

## Task 5: Endpoints (topup, wallet, webhook, history)

**Files:** Modify `api/main.py`.

- [ ] **Step 1: Imports + tag.** In `api/main.py`: add `import uuid` near the other stdlib imports; change the FastAPI import to include `Header`:
```python
from fastapi import FastAPI, Header, HTTPException, Query
```
Add the wallet modules + models to the imports:
```python
from . import xendit
from . import wallet_repo as wallet
from .models import (
    EVModel, EVModelList, GeoJSONFeatureCollection, Health, NameCount,
    NearestStationRoute, Route, SourceCount, SpeedTier, Station,
    StationList, Stats, Topup, TopupCreated, TopupRequest, WalletBalance,
)
```
Add a tag to the `TAGS` list:
```python
    {"name": "wallet", "description": "Wallet balance + Xendit top-up (payment)."},
```

- [ ] **Step 2: Add the endpoints** (anywhere after the `ev_model` endpoint, before `/api/v1/stats` is fine):
```python
@app.post("/api/v1/wallet/topup", response_model=TopupCreated, tags=["wallet"],
          summary="Create a Xendit invoice to top up the wallet",
          responses={502: {"description": "Payment provider error"}})
def wallet_topup(body: TopupRequest) -> TopupCreated:
    external_id = f"topup-{uuid.uuid4()}"
    try:
        inv = xendit.create_invoice(external_id, body.amount_idr, "EV-FLOW wallet top-up")
    except xendit.XenditError as e:
        raise HTTPException(502, f"payment provider error: {e}")
    row = wallet.create_topup(body.amount_idr, external_id, inv["id"], inv["invoice_url"])
    return TopupCreated(**row)


@app.get("/api/v1/wallet", response_model=WalletBalance, tags=["wallet"], summary="Wallet balance")
def get_wallet() -> WalletBalance:
    w = wallet.get_wallet()
    return WalletBalance(balance_idr=w["balance_idr"], updated_at=w["updated_at"])


@app.post("/api/v1/webhooks/xendit", tags=["wallet"],
          summary="Xendit invoice webhook (credits the wallet on PAID)",
          responses={401: {"description": "Invalid callback token"}})
def xendit_webhook(payload: dict, x_callback_token: Optional[str] = Header(None)):
    expected = os.getenv("XENDIT_CALLBACK_TOKEN", "")
    if not expected or x_callback_token != expected:
        raise HTTPException(401, "invalid callback token")
    if payload.get("status") == "PAID" and payload.get("id"):
        wallet.mark_paid_and_credit(payload["id"])
    return {"ok": True}


@app.get("/api/v1/wallet/topups", response_model=list[Topup], tags=["wallet"],
         summary="Recent top-ups")
def wallet_topups(limit: int = Query(20, ge=1, le=100)) -> list[Topup]:
    return [Topup(**t) for t in wallet.list_topups(limit)]
```

- [ ] **Step 3: Compile + import** `.venv/bin/python -m py_compile api/main.py && .venv/bin/python -c "import api.main; print(sorted(r.path for r in api.main.app.routes if 'wallet' in getattr(r,'path','') or 'webhooks' in getattr(r,'path','')))"`. Expect the four new paths.

- [ ] **Step 4: Run the no-DB suite** `.venv/bin/python -m pytest -q` → all pass / DB tests skip (booting `api.main` must not require a DB).

- [ ] **Step 5: Commit**
```bash
git add api/main.py
git commit -m "feat: wallet topup/balance/webhook/history endpoints"
```

---

## Task 6: DB-gated integration tests

**Files:** Create `tests/test_wallet_db.py`.

- [ ] **Step 1: Write `tests/test_wallet_db.py`:**
```python
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
```

- [ ] **Step 2: Run the no-DB suite** `.venv/bin/python -m pytest -q` → these 3 report `skipped` (no DATABASE_URL); everything else passes.

- [ ] **Step 3: Commit**
```bash
git add tests/test_wallet_db.py
git commit -m "test: wallet topup + webhook (credit, idempotency, token, min)"
```

---

## Task 7: Config + deploy wiring + spec regen

**Files:** Modify `.env.example`, `.env.deploy.example`, `podman-compose.yml`; regenerate `openapi.json`/`openapi.yaml`.

- [ ] **Step 1: `.env.example`** append (placeholders, no real values):
```
# Xendit (payment). Backend uses the SECRET key; public key is frontend-only.
XENDIT_SECRET_KEY=your_xendit_development_secret_key
XENDIT_PUBLIC_KEY=your_xendit_development_public_key
XENDIT_BASE_URL=https://api.xendit.co
XENDIT_CALLBACK_TOKEN=set_a_webhook_verification_token
```

- [ ] **Step 2: `.env.deploy.example`** append the same four lines (placeholders).

- [ ] **Step 3: `podman-compose.yml`** add to the `api` service `environment:` block:
```yaml
      XENDIT_SECRET_KEY: "${XENDIT_SECRET_KEY:-}"
      XENDIT_BASE_URL: "${XENDIT_BASE_URL:-https://api.xendit.co}"
      XENDIT_CALLBACK_TOKEN: "${XENDIT_CALLBACK_TOKEN:-}"
```

- [ ] **Step 4: Regenerate spec** `.venv/bin/python -m api.export_openapi` and confirm the wallet paths exist:
```bash
.venv/bin/python -c "import json; p=json.load(open('openapi.json'))['paths']; print(all(k in p for k in ['/api/v1/wallet','/api/v1/wallet/topup','/api/v1/webhooks/xendit']))"
```
Expect `True`.

- [ ] **Step 5: Commit**
```bash
git add .env.example .env.deploy.example podman-compose.yml openapi.json openapi.yaml
git commit -m "feat: wire XENDIT_* env + regen spec"
```

---

## Task 8: End-to-end verification against real Postgres

**Files:** none (verification only). Xendit is mocked; the real sandbox call is a manual check noted at the end.

- [ ] **Step 1: Start Postgres + migrate + run the full suite with the DB**
```bash
podman rm -f evflow-db-test 2>/dev/null
podman run -d --name evflow-db-test -p 55432:5432 -e POSTGRES_USER=evflow -e POSTGRES_PASSWORD=evflow -e POSTGRES_DB=evflow docker.io/postgis/postgis:16-3.4
export DATABASE_URL="postgresql+psycopg://evflow:evflow@localhost:55432/evflow"
# wait until it accepts connections, then:
.venv/bin/alembic upgrade head            # applies 0001 + 0002
.venv/bin/python -m pytest -q             # all pass incl wallet DB tests
```
Expect: migrations apply, full suite green.

- [ ] **Step 2: Live smoke (Xendit mocked via a tiny inline patch)** is covered by the pytest run in Step 1. Confirm a topup + webhook credits the wallet there.

- [ ] **Step 3: Tear down** `podman rm -f evflow-db-test`.

- [ ] **Step 4 (manual, optional): real sandbox check.** With the real `XENDIT_SECRET_KEY` set in `.env`, `POST /api/v1/wallet/topup` should return a real `invoice_url` on `checkout.xendit.co`. Paying it in sandbox triggers the real webhook to `/api/v1/webhooks/xendit`. This requires the deployed VPS + the webhook registered in the Xendit dashboard; it is not part of the automated suite.

---

## Self-review

- **Spec coverage:** topup endpoint + invoice (Task 1,5) · webhook verify + idempotent credit (Task 3 `mark_paid_and_credit`, Task 5, Task 6) · wallet balance (Task 3,5) · tables + singleton seed (Task 2) · env config + compose + deploy (Task 7) · mockable client + DB-gated tests (Task 1,6) · min amount 10000 (Task 4 `ge=10000`, Task 6). All covered.
- **Placeholder scan:** none.
- **Type/name consistency:** `create_invoice(external_id, amount_idr, description) -> {id, invoice_url, status}`; `wallet.create_topup(amount_idr, external_id, invoice_id, invoice_url)`; `mark_paid_and_credit(invoice_id) -> bool`; models `TopupRequest/TopupCreated/WalletBalance/Topup`; endpoints use these names consistently across tasks.
