# Xendit wallet top-up (EV-FLOW Epic 6.0, payment slice 1)

**Date:** 2026-06-03
**Status:** Approved design, pending implementation plan

## Context

The full charging + payment journey (scan QR, initiate charging, pay a deposit, charge, settle
the actual cost and refund the difference to a wallet, receipt) is Epic 6.0. It is too large for
one spec, so it is split into slices:

1. **Wallet + top-up via Xendit** (this spec): a global demo wallet that is topped up through
   Xendit's hosted Invoice in sandbox mode. The money foundation.
2. Tariff + charging session + deposit (debit wallet) + settlement and refund (internal wallet ops).
3. Scan QR + live charging status + consolidated receipt.

Decisions locked during brainstorming:
- Xendit is used for **wallet top-up only**. Charging (slice 2) debits/credits the wallet internally.
- A single **global demo wallet** (no users or auth yet). This is intentionally insecure (anyone can
  top up the one shared balance) and acceptable only for the sandbox demo, not production.
- **Xendit Invoice API**.
- **Real Xendit sandbox** (development key), not a built-in mock. Tests mock the HTTP client.

## Goals / success criteria

- `POST /api/v1/wallet/topup` creates a Xendit invoice for a top-up amount, records a `pending`
  topup, and returns the hosted `invoice_url`.
- Xendit's webhook (verified by the callback token) marks the topup `paid` and credits the global
  wallet **exactly once** (idempotent under duplicate deliveries).
- `GET /api/v1/wallet` returns the current balance.
- The Xendit client is configured by env and mockable in tests (the test suite makes no network calls).
- DB-gated integration tests cover top-up creation, webhook crediting, idempotency, and bad-token rejection.

## Architecture

```
client --POST /wallet/topup--> api --create invoice--> Xendit sandbox
                                  |<-- invoice_url ----------|
client opens invoice_url, pays in Xendit's hosted page (sandbox)
Xendit --POST /webhooks/xendit (x-callback-token)--> api --credit--> wallet (Postgres)
```

- Two new tables in the existing Postgres: `wallet` (singleton) and `topups`.
- `api/xendit.py`: a thin client over the Xendit Invoice API using `httpx`, configured from env.
- Endpoints under `/api/v1/wallet` and `/api/v1/webhooks/xendit`.

## Schema (Alembic migration 0002)

`wallet` (single row, the global demo wallet):
- `id smallint PRIMARY KEY` (always 1)
- `balance_idr bigint NOT NULL DEFAULT 0 CHECK (balance_idr >= 0)`
- `updated_at timestamptz NOT NULL DEFAULT now()`
- migration seeds one row `(1, 0)`.

`topups`:
- `id uuid PRIMARY KEY`
- `external_id text NOT NULL UNIQUE` (our id sent to Xendit)
- `xendit_invoice_id text UNIQUE` (Xendit's invoice id)
- `amount_idr bigint NOT NULL CHECK (amount_idr > 0)`
- `status text NOT NULL DEFAULT 'pending'` (`pending` | `paid` | `expired`)
- `invoice_url text`
- `created_at timestamptz NOT NULL DEFAULT now()`
- `paid_at timestamptz`

## Xendit client (`api/xendit.py`)

`create_invoice(external_id, amount_idr, description) -> {id, invoice_url, status}`:
- `POST {XENDIT_BASE_URL}/v2/invoices` with HTTP Basic auth (secret key as username, empty password).
- Body: `external_id`, `amount`, `description`, `currency: "IDR"`.
- Raises a clear error on non-2xx.

Config from env: `XENDIT_SECRET_KEY`, `XENDIT_BASE_URL` (default `https://api.xendit.co`),
`XENDIT_CALLBACK_TOKEN`. Tests monkeypatch `create_invoice` so no network is hit.

## Endpoints

`POST /api/v1/wallet/topup` body `{ "amount_idr": 50000 }` (min 10000, Xendit's floor):
- generate `external_id` (uuid), call `xendit.create_invoice`, insert `topups` (`pending`, with
  `invoice_url` + `xendit_invoice_id`), return `{ topup_id, amount_idr, status: "pending", invoice_url }`.

`GET /api/v1/wallet` -> `{ balance_idr, currency: "IDR", updated_at }`.

`POST /api/v1/webhooks/xendit` (Xendit invoice callback):
- Verify header `x-callback-token` equals `XENDIT_CALLBACK_TOKEN`; mismatch -> `401`.
- Body carries the invoice `id` and `status`. When `status == "PAID"`:
  atomically `UPDATE topups SET status='paid', paid_at=now() WHERE xendit_invoice_id=:id AND status='pending' RETURNING amount_idr`;
  if a row is returned, credit `wallet.balance_idr` by that amount in the same transaction.
- Idempotent: a second delivery finds no `pending` row, so it credits nothing and returns `200`.
- Unknown invoice or non-PAID status: `200` no-op (so Xendit stops retrying).

`GET /api/v1/wallet/topups` (optional): recent topups for visibility/debugging.

## Idempotency + security

- Credit exactly once via the conditional `UPDATE ... WHERE status='pending' RETURNING`, all inside
  one DB transaction with the wallet credit.
- Verify `x-callback-token`; reject mismatch with `401`.
- Validate `amount_idr > 0` and `>= 10000`.
- The wallet only gains balance in this slice; spending arrives in slice 2.

## Config (env)

`XENDIT_SECRET_KEY`, `XENDIT_BASE_URL`, `XENDIT_CALLBACK_TOKEN`. Add placeholders to `.env.example`
and `.env.deploy.example`, and wire them into `podman-compose.yml` (`api` `environment:` as
`${XENDIT_...}`). Real values live only in `.env` (gitignored) and on the VPS.

## Testing

- Unit: amount validation; the client builds the right request (httpx layer monkeypatched).
- Integration (DB-gated, `xendit.create_invoice` mocked):
  - topup creates a `pending` row and returns `invoice_url`;
  - webhook with valid token + `PAID` flips status to `paid` and credits the wallet;
  - a duplicate identical webhook does not double-credit;
  - wrong token -> `401`;
  - unknown invoice -> `200` no-op.

## Deployment

Set `XENDIT_*` on the VPS `.env`; register the webhook URL
`https://ev-flow-api.opensoft.id/api/v1/webhooks/xendit` in the Xendit dashboard with the same
callback token. Run `alembic upgrade head` to create the tables.

## Out of scope

- Slice 2 (tariff, charging session, deposit debit, settlement and refund) and slice 3 (QR, live
  status, receipt).
- User accounts, auth, and per-user wallets (one global wallet for now).
- Per-session Xendit charges and Xendit refunds (later refunds are internal wallet credits).
- Spending or debiting the wallet (slice 2).
