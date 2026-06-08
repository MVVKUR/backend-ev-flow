# Authentication and accounts (EV-FLOW sub-project #2)

**Date:** 2026-06-08
**Status:** Approved design, pending implementation plan

## Context

The frontend has Login, Register, and Complete Profile screens. The backend has no notion of
users yet: stations, wallet, and connector data are all served without login. This sub-project
adds accounts and authentication for the EV User (Driver) role. The Business Planner role and its
SiteGrid/BPS dashboard are out of scope; we only store the `account_type` so it is extensible.

Decisions locked during brainstorming:
- Two sign-in methods: **username + password** and **Google OAuth**.
- Google uses the **Authorization Code redirect flow** (backend-driven): `/auth/google/login`
  redirects to Google, `/auth/google/callback` exchanges the code with the client secret, then the
  backend issues its own JWT and redirects back to the frontend.
- **JWT access tokens only** (no refresh tokens for the MVP).
- A Google sign-up creates a minimal user; the EV profile (vehicle, connector, consent) is filled
  later via `PATCH /users/me`. A `profile_completed` flag tells the frontend whether to show the
  Complete Profile screen. The password Register form fills everything at once.
- The **wallet is not touched** in this slice (it stays the global/default wallet); making it
  per-user is a later slice.

## Goals / success criteria

- A user can register with username + password and immediately receive a JWT.
- A user can log in with username + password and receive a JWT.
- A user can sign in with Google: the callback creates or finds the user and redirects to the
  frontend with a JWT.
- `GET /users/me` returns the current user (from the Bearer JWT); `PATCH /users/me` updates the
  profile and recomputes `profile_completed`.
- Passwords are stored only as bcrypt hashes; the JWT is signed with a server secret; the Google
  callback validates an HMAC-signed `state` for CSRF.
- Unit tests (no network/DB) cover hashing, JWT, and state signing; DB-gated tests cover the full
  register/login/me/patch flow and a mocked Google callback.

## Schema (Alembic migration 0004)

`users`:
- `id uuid PRIMARY KEY`
- `username text UNIQUE` (nullable; primary identity for password login; Google users may set it later)
- `password_hash text` (nullable; null for Google-only users)
- `google_sub text UNIQUE` (nullable; Google's stable user id)
- `email text` (nullable; from Google)
- `full_name text`
- `account_type text NOT NULL DEFAULT 'ev_user'` (`ev_user` | `business_planner`)
- `ev_model_id text` (nullable; references the EV catalogue id from `/api/v1/ev-models`)
- `main_connector_type text` (nullable; e.g. `CCS2`)
- `location_consent boolean NOT NULL DEFAULT false`
- `location_consent_at timestamptz` (nullable)
- `profile_completed boolean NOT NULL DEFAULT false`
- `created_at timestamptz NOT NULL DEFAULT now()`

`profile_completed` is computed by the app: true when `ev_model_id`, `main_connector_type`, and
`location_consent = true` are all present.

## Security helpers (`api/security.py`)

- `hash_password(plain) -> str` and `verify_password(plain, hash) -> bool` using **bcrypt**.
- `create_access_token(user_id) -> str` and `decode_access_token(token) -> user_id` using
  **PyJWT** (HS256, `sub = user_id`, `exp` from `JWT_EXPIRE_MINUTES`, signed with `JWT_SECRET`).
- `sign_state() -> str` and `verify_state(state) -> bool`: an HMAC-signed random nonce for Google
  CSRF (stateless; proves the backend issued the state).
- `current_user` FastAPI dependency: reads `Authorization: Bearer <jwt>`, decodes it, loads the
  user; raises 401 on a missing/invalid token and 401 if the user no longer exists.

## Google OAuth (`api/google_oauth.py`)

- `build_auth_url(state) -> str`: Google authorize URL with `client_id`, `redirect_uri`,
  `response_type=code`, `scope=openid email profile`, and `state`.
- `exchange_code(code) -> dict`: POST to `https://oauth2.googleapis.com/token` (with
  `client_secret`) to get an access token, then GET `https://www.googleapis.com/oauth2/v3/userinfo`
  to return `{sub, email, name}`. Uses `httpx` (already a dependency). Raises a clear error on
  failure. Tests monkeypatch `exchange_code`.
- Account mapping: match by `google_sub` only. A new `google_sub` creates a new user
  (`profile_completed = false`). Email-based linking is deferred (avoids account-takeover risk).

## Users repository (`api/users_repo.py`)

`create_user(...)`, `get_by_username(username)`, `get_by_google_sub(sub)`, `get_by_id(id)`,
`update_profile(id, fields)`. SQL via the existing `engine`, same style as `stations_repo` /
`wallet_repo`.

## Endpoints (all under `/api/v1`, tag `auth`)

- `POST /auth/register` body `{username, password, full_name, ev_model_id?, main_connector_type?, location_consent?}`
  -> create user (password hashed), compute `profile_completed`, return `TokenResponse`. `409` if
  username taken; `422` if password shorter than 8 chars.
- `POST /auth/login` body `{username, password}` -> verify, return `TokenResponse`. `401` on bad
  credentials or a user with no password (Google-only).
- `GET /auth/google/login` -> `302` to `build_auth_url(sign_state())`.
- `GET /auth/google/callback?code&state` -> `verify_state` (else `400`); `exchange_code`;
  find-or-create user by `google_sub`; issue JWT; `302` redirect to
  `FRONTEND_URL/auth/callback#token=<jwt>`.
- `GET /users/me` (auth) -> `UserPublic`.
- `PATCH /users/me` (auth) body `{username?, ev_model_id?, main_connector_type?, location_consent?}`
  -> update provided fields, set `location_consent_at` when consent flips true, recompute
  `profile_completed`, return `UserPublic`. `409` if a new username is taken.

## Models (`api/models.py`)

`RegisterRequest`, `LoginRequest`, `ProfileUpdate`, `UserPublic`
(`id, username, full_name, email, account_type, ev_model_id, main_connector_type, location_consent,
profile_completed, created_at`), and `TokenResponse` (`access_token, token_type="bearer", user`).

## Config (env)

`JWT_SECRET`, `JWT_EXPIRE_MINUTES`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`GOOGLE_REDIRECT_URI`, `FRONTEND_URL`. Add placeholders to `.env.example` / `.env.deploy.example`
and wire them into `podman-compose.yml`. Real values live only in `.env` (gitignored) and on the VPS.

## Dependencies

Add `bcrypt` and `PyJWT` to `requirements-api.txt`. `httpx` is already present.

## Testing

- Unit (`tests/test_security.py`): bcrypt hash/verify (and wrong-password); JWT create/decode plus
  invalid-token rejection; `sign_state`/`verify_state` plus a tampered state.
- DB-gated (`tests/test_auth_db.py`, monkeypatch `google_oauth.exchange_code`): register returns a
  token; login works; wrong password -> `401`; duplicate username -> `409`; `GET /users/me` with
  the token; `PATCH /users/me` completes the profile (`profile_completed` becomes true); `/users/me`
  without a token -> `401`; Google callback creates a user and redirects with a token.

## Out of scope (deferred)

- Per-user wallet (the wallet stays global/default; wiring it to `current_user` is a later slice).
- Business Planner role features (SiteGrid dashboard, BPS spatial analysis).
- Password reset and email verification (no email service yet).
- Refresh tokens (access token only).
- Email-based account linking between password and Google identities.
