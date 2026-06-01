# Database foundation + station migration (EV-FLOW sub-project 1)

**Date:** 2026-06-01
**Status:** Approved design, pending implementation plan

## Context

The EV-FLOW backend currently loads its charging-station data (PLN SPKLU + Open Charge Map +
OpenStreetMap, ~3,569 rows) from JSON files into an in-memory pandas DataFrame at startup.
There is no database. That is fine for read-only discovery, but the project needs a real
database for the accounts and payment features (users, wallet, sessions, transactions) that
must persist and be written per user, and the Data Management Plan specifies PostgreSQL with
PostGIS for the spatial data.

This is **sub-project 1 of 3**:

1. **DB foundation + stations** (this spec): stand up PostgreSQL/PostGIS, migrate the stations
   into it (deduplicated), and point the API at the database.
2. Accounts and auth (User, Wallet, Vehicle): later, builds on #1.
3. Sessions and payment with Xendit (ChargingSession, Transaction, Tariff): later, builds on #1 and #2.

Sub-projects 2 and 3 are out of scope here. This spec delivers the database foundation that the
other two sit on, plus the station data living in it.

## Goals / success criteria

- PostgreSQL + PostGIS runs as a container alongside the API and persists data across restarts.
- The ~3,569 source rows are deduplicated into unique physical stations (expected ~1,147) and
  stored in the database, each carrying a list of contributing `sources`.
- All existing station-facing endpoints return the same data they do today, served from the
  database instead of in-memory pandas, with `/nearby` backed by a PostGIS spatial query.
- The connector-type inference and speed-tier classification already in the code are computed at
  load time and stored as columns.
- The deployment (podman-compose, host networking) gains the database; `DEPLOY.md` documents the
  start/seed/run order.
- The test suite still runs without a database (DB-backed tests skip when `DATABASE_URL` is unset);
  the dedup logic is covered by pure unit tests.

## Architecture

```
[ Cloudflare Tunnel ] --> api (uvicorn, host network) --SQL--> db (postgis/postgis, host network)
                                                                  |
                                                              pgdata volume (persists)
```

- A `postgis/postgis` container runs next to the API. With host networking on the LXC VPS, the
  API reaches it at `localhost:5432`.
- Postgres is the source of truth. The API queries it live on each request (no in-memory cache);
  for ~1,147 indexed rows this is sub-millisecond and is the pattern the accounts/transactions
  work will reuse.
- Configuration is a single `DATABASE_URL` environment variable.

## Database schema

One denormalized `stations` table for now (normalizing into separate operator/connector tables
is deferred; it adds joins without value for read-only discovery):

| Column | Type | Notes |
|---|---|---|
| `id` | text PK | id of the kept (anchor) station, e.g. `pln_spklu-1` |
| `geom` | `geometry(Point, 4326)` | location; GiST-indexed |
| `name` | text | |
| `address` | text | |
| `province` | text | indexed |
| `city` | text | |
| `operator` | text | |
| `power_kw` | double precision | nullable |
| `speed_tier` | text | indexed; slow / medium / fast / ultra_fast |
| `connector_types` | text[] | inferred (e.g. `{CCS2}` or `{"AC Type 2"}`) |
| `connector_inferred` | boolean | true while values are inferred |
| `sources` | text[] | contributing datasets, e.g. `{pln_spklu,open_charge_map}` |
| `status` | text | |
| `date_verified` | text | |

Indexes: GiST on `geom`, btree on `province` and `speed_tier`.

## Deduplication + seed (ETL)

A one-shot script (`scripts/seed_db.py`, runnable as `python -m scripts.seed_db`) that:

1. Reuses the existing source normalization (`_load_pln` / `_load_ocm` / `_load_osm`) to get the
   ~3,569 normalized rows. The shared normalization moves into a module the seed and the data
   layer both import, so there is one copy.
2. Applies the existing connector inference and speed-tier classification (`api/connectors.py`).
3. Clusters points within ~75 m into one station (deterministic greedy clustering, PLN rows
   anchor clusters first, then OCM, then OSM). Merge rules: take name/address/operator/province/
   city as the first non-null in that source priority; `power_kw` = max; `connector_types` = union;
   `sources` = the set of contributing sources; `status` prefers `operational`; `id` = the anchor's id.
4. Loads the result into Postgres (idempotent: truncate then insert).

Expected output: ~1,147 unique stations.

The clustering is a **pure function** (`cluster_stations(rows) -> merged_rows`) so it can be unit-tested
without a database.

## Data access layer

- `api/db.py`: SQLAlchemy 2.0 engine + session factory built from `DATABASE_URL`.
- `api/stations_repo.py`: query functions returning rows that map onto the existing Pydantic models:
  `list_stations(filters, limit, offset)`, `get_station(id)`, `nearby(lat, lon, radius_km, limit)`,
  `stats()`, `connector_counts()`, `speed_tier_counts()`, `provinces()`, `cities(province)`,
  `source_counts()`.
- `/nearby` uses PostGIS: filter with `ST_DWithin(geom::geography, point::geography, radius_m)`,
  order by `ST_Distance`, and return distance in km.
- `main.py` endpoints call the repo and build the response models. Filters become SQL `WHERE`
  clauses; stats/connectors/speed-tiers become `GROUP BY`. The routing endpoints fetch station
  coordinates from the repo instead of the pandas DataFrame.
- The old in-memory `data.load()` path is removed once the repo is in place (the normalization
  helpers it used are retained for the seed script).

New runtime dependencies: `sqlalchemy`, `psycopg[binary]`. `alembic` for migrations. (No new
heavy/geo packages; PostGIS lives in the database container, not the Python image.)

## API contract change

Deduplication means a station has multiple sources, so the single `source` field becomes a list:

- `Station.source` (single enum) becomes `Station.sources: list[str]`.
- The `?source=` filter now means "stations that include this source" (matches against `sources`).
- `/api/v1/sources` still returns per-source counts (a station counts toward each of its sources).
- `FRONTEND_API.md` is updated to document `sources` and the changed filter semantics.

This is acceptable because the frontend has not been built yet; changing it now avoids a breaking
change later.

## Migrations

Alembic manages the schema so sub-projects 2 and 3 can add their tables cleanly. The initial
migration enables the PostGIS extension and creates the `stations` table with its indexes.

## Deployment

- `podman-compose.yml` gains a `db` service (`postgis/postgis`), a `pgdata` named volume, and
  passes `DATABASE_URL` to the API. Host networking is kept (LXC-friendly).
- Start order: bring up `db`, run migrations, run `python -m scripts.seed_db` once (the data lives
  in the host-mounted `data/raw`), then the API serves from the database.
- `DEPLOY.md` documents this, plus a note to keep port 5432 closed to the public (only the API,
  via the Cloudflare Tunnel, is exposed; the database is reachable only on the host).

## Testing

- **Unit:** `cluster_stations` (dedup/merge rules, 75 m boundary, source priority) as a pure
  function; the existing connector/speed-tier and Dijkstra unit tests are unchanged.
- **Integration:** station endpoint tests run against a real Postgres and are skipped when
  `DATABASE_URL` is not set, so the suite still passes on a machine without a database. A short
  doc note explains how to spin up a throwaway Postgres for the full run.

## Out of scope

- Sub-project 2 (accounts/auth) and sub-project 3 (sessions/payment with Xendit) tables and endpoints.
- Normalizing stations into separate operator/connector/tariff tables.
- Replacing inferred connector types with real Google Places data.
- The routing road graph (unchanged; still a cached GraphML file).
