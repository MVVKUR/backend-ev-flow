# Database Foundation + Station Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the charging-station data out of in-memory pandas into PostgreSQL/PostGIS (deduplicated to unique stations), and serve every station endpoint from the database with a PostGIS-backed `/nearby`.

**Architecture:** A `postgis/postgis` container runs beside the API (host networking; API reaches it at `localhost:5432`). A seed script reuses the existing source normalization + connector inference, clusters points within 75 m into unique stations, and loads them into a single denormalized `stations` table. The API queries Postgres live via SQLAlchemy; a thin repository module isolates the SQL.

**Tech Stack:** PostgreSQL 16 + PostGIS, SQLAlchemy 2.0 (psycopg 3 driver), Alembic (migrations), FastAPI, pytest.

**Spec:** `docs/superpowers/specs/2026-06-01-database-stations-design.md`

---

## File structure

| File | Responsibility | New/Modify |
|---|---|---|
| `requirements-api.txt`, `requirements.txt` | add sqlalchemy, psycopg, alembic | Modify |
| `api/db.py` | SQLAlchemy engine/session from `DATABASE_URL` | Create |
| `api/dedup.py` | pure `cluster_stations()` 75 m merge | Create |
| `api/sources.py` | source loaders (moved from data.py) + `normalized_rows()` | Create |
| `api/stations_repo.py` | all station SQL queries | Create |
| `scripts/seed_db.py` | normalize -> cluster -> insert | Create |
| `alembic/` + `alembic.ini` | migrations (PostGIS + stations table) | Create |
| `api/models.py` | `Station.source` -> `Station.sources` | Modify |
| `api/main.py` | endpoints call the repo, not `data.load()` | Modify |
| `api/data.py` | remove pandas `load()`/`_DF`; keep `haversine_km` | Modify |
| `api/routing.py` | station coords come from the repo | Modify |
| `podman-compose.yml`, `DEPLOY.md`, `FRONTEND_API.md` | db service + docs | Modify |
| `tests/test_dedup.py` | unit tests for clustering | Create |
| `tests/test_stations_db.py` | DB-gated endpoint tests | Create |
| `tests/test_connector_endpoints.py`, `tests/test_route_endpoint.py` | switch off the removed in-memory path | Modify |

---

## Task 1: Dependencies + DB connection module

**Files:**
- Modify: `requirements-api.txt`, `requirements.txt`
- Create: `api/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Add dependencies.** Append to `requirements-api.txt`:

```
sqlalchemy>=2.0
psycopg[binary]>=3.1
alembic>=1.13
```

Add the same three lines under the API section of `requirements.txt`.

- [ ] **Step 2: Install into the venv**

Run: `.venv/bin/pip install "sqlalchemy>=2.0" "psycopg[binary]>=3.1" "alembic>=1.13"`
Expected: installs without error.

- [ ] **Step 3: Write `api/db.py`**

```python
"""SQLAlchemy engine + session, configured from DATABASE_URL."""
from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://evflow:evflow@localhost:5432/evflow",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)
```

- [ ] **Step 4: Write the test** `tests/test_db.py`

```python
import pytest

pytest.importorskip("sqlalchemy")


def test_engine_uses_psycopg_driver(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/x")
    import importlib
    from api import db
    importlib.reload(db)
    assert db.engine.url.drivername == "postgresql+psycopg"
    assert db.engine.url.database == "x"
```

- [ ] **Step 5: Run it**

Run: `.venv/bin/python -m pytest tests/test_db.py -v`
Expected: PASS (create_engine is lazy, no live DB needed).

- [ ] **Step 6: Commit**

```bash
git add requirements-api.txt requirements.txt api/db.py tests/test_db.py
git commit -m "feat: add SQLAlchemy engine + DB dependencies"
```

---

## Task 2: Dedup clustering (pure core, full TDD)

**Files:**
- Create: `api/dedup.py`
- Test: `tests/test_dedup.py`

- [ ] **Step 1: Write the failing tests** `tests/test_dedup.py`

```python
import pytest

from api.dedup import cluster_stations


def _row(id, source, lat, lon, power=None, name=None, conns=None):
    return {"id": id, "source": source, "latitude": lat, "longitude": lon,
            "power_kw": power, "name": name, "address": None, "province": None,
            "city": None, "operator": None, "charge_type": None, "status": None,
            "date_verified": None, "connector_types": conns or []}


@pytest.mark.unit
def test_two_points_within_75m_merge():
    # ~30 m apart in Jakarta
    a = _row("pln_spklu-1", "pln_spklu", -6.2000, 106.8000, power=22, conns=["AC Type 2"])
    b = _row("open_charge_map-9", "open_charge_map", -6.20020, 106.8000, power=150, conns=["CCS2"])
    out = cluster_stations([a, b])
    assert len(out) == 1
    s = out[0]
    assert s["id"] == "pln_spklu-1"                 # PLN anchors
    assert sorted(s["sources"]) == ["open_charge_map", "pln_spklu"]
    assert s["power_kw"] == 150                       # max
    assert sorted(s["connector_types"]) == ["AC Type 2", "CCS2"]  # union


@pytest.mark.unit
def test_points_over_75m_stay_separate():
    a = _row("pln_spklu-1", "pln_spklu", -6.2000, 106.8000)
    b = _row("pln_spklu-2", "pln_spklu", -6.2050, 106.8050)  # ~700 m away
    out = cluster_stations([a, b])
    assert len(out) == 2


@pytest.mark.unit
def test_descriptive_fields_fill_from_first_nonnull_by_priority():
    a = _row("pln_spklu-1", "pln_spklu", -6.2000, 106.8000, name="PLN Gambir")
    b = _row("open_charge_map-9", "open_charge_map", -6.20010, 106.8000, name="OCM name")
    a["address"] = None
    b["address"] = "Jl. Test 1"
    out = cluster_stations([a, b])
    assert out[0]["name"] == "PLN Gambir"        # PLN wins (anchor)
    assert out[0]["address"] == "Jl. Test 1"     # filled from OCM (PLN was null)


@pytest.mark.unit
def test_deterministic_pln_anchors_regardless_of_input_order():
    a = _row("pln_spklu-1", "pln_spklu", -6.2000, 106.8000)
    b = _row("osm-node-5", "osm", -6.20010, 106.8000)
    assert cluster_stations([b, a])[0]["id"] == "pln_spklu-1"
    assert cluster_stations([a, b])[0]["id"] == "pln_spklu-1"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_dedup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'api.dedup'`.

- [ ] **Step 3: Implement `api/dedup.py`**

```python
"""Deduplicate charging stations: merge points within a radius into one station.

Pure functions, no I/O. Rows are processed in source-priority order (PLN, then
Open Charge Map, then OSM) so a PLN row anchors each cluster, making the result
deterministic. Each input row is a normalized dict with at least: id, source,
latitude, longitude, power_kw, connector_types, and the descriptive fields.
"""
from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
from typing import Optional

MERGE_RADIUS_M = 75.0
SOURCE_PRIORITY = {"pln_spklu": 0, "open_charge_map": 1, "osm": 2}
_DESC_FIELDS = ("name", "address", "province", "city", "operator",
                "charge_type", "status", "date_verified")


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371008.8  # mean earth radius, metres
    p1, p2 = radians(lat1), radians(lat2)
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(p1) * cos(p2) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


def _priority(row: dict) -> int:
    return SOURCE_PRIORITY.get(row.get("source"), 99)


def cluster_stations(rows: list[dict], radius_m: float = MERGE_RADIUS_M) -> list[dict]:
    """Merge rows whose coordinates are within radius_m of a cluster's anchor."""
    ordered = sorted(rows, key=lambda r: (_priority(r), str(r.get("id"))))
    clusters: list[dict] = []
    for r in ordered:
        anchor = None
        for c in clusters:
            if _haversine_m(r["latitude"], r["longitude"], c["latitude"], c["longitude"]) <= radius_m:
                anchor = c
                break
        if anchor is None:
            clusters.append(_new_cluster(r))
        else:
            _merge_into(anchor, r)
    for c in clusters:
        _finalize(c)
    return clusters


def _new_cluster(r: dict) -> dict:
    c = dict(r)
    c["sources"] = [r["source"]]
    c["_powers"] = [r.get("power_kw")]
    c["_conns"] = list(r.get("connector_types") or [])
    return c


def _merge_into(c: dict, r: dict) -> None:
    if r["source"] not in c["sources"]:
        c["sources"].append(r["source"])
    for f in _DESC_FIELDS:
        if not c.get(f) and r.get(f):
            c[f] = r[f]
    c["_powers"].append(r.get("power_kw"))
    for t in (r.get("connector_types") or []):
        if t not in c["_conns"]:
            c["_conns"].append(t)


def _finalize(c: dict) -> None:
    powers = [p for p in c.pop("_powers") if p is not None]
    c["power_kw"] = max(powers) if powers else None
    c["connector_types"] = c.pop("_conns")
    c["connector_inferred"] = True
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_dedup.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add api/dedup.py tests/test_dedup.py
git commit -m "feat: add 75m station deduplication (pure, tested)"
```

---

## Task 3: Source normalization module

**Files:**
- Create: `api/sources.py`
- Modify: `api/data.py`
- Test: `tests/test_sources.py`

- [ ] **Step 1: Move the loaders.** Cut the three functions `_load_pln`, `_load_ocm`, `_load_osm` and the helper `_num`, plus the `ROOT`, `RAW_DIR`, `PLN_PATH`, `OCM_PATH`, `OSM_PATH`, `COLUMNS` constants and the `json`/`os`/`Path` imports they need, **verbatim** from `api/data.py` into a new `api/sources.py`. Keep their bodies unchanged.

- [ ] **Step 2: Add `normalized_rows()` to `api/sources.py`** (below the moved loaders):

```python
import math

from . import connectors


def _clean_power(p):
    if p is None:
        return None
    if isinstance(p, float) and math.isnan(p):
        return None
    return float(p)


def normalized_rows() -> list[dict]:
    """All source rows, normalized, with inferred connector_types + speed_tier."""
    rows = _load_pln() + _load_ocm() + _load_osm()
    out = []
    for r in rows:
        if r.get("latitude") is None or r.get("longitude") is None:
            continue
        r["power_kw"] = _clean_power(r.get("power_kw"))
        r["speed_tier"] = connectors.speed_tier(r.get("power_kw"), r.get("charge_type"))
        r["connector_types"] = connectors.infer_connectors(r.get("power_kw"), r.get("charge_type"))
        out.append(r)
    return out
```

- [ ] **Step 3: Trim `api/data.py`.** Remove the now-moved loaders/constants and the `load()`, `reload()`, and `_DF` global (they are replaced by the DB). **Keep `haversine_km`** (routing uses it) and keep the file importable. `api/data.py` should now contain only `haversine_km` and its `numpy` import.

- [ ] **Step 4: Write the test** `tests/test_sources.py`

```python
import math

import pytest

from api import sources


@pytest.mark.unit
def test_normalized_rows_infers_connector_and_speed(monkeypatch):
    monkeypatch.setattr(sources, "_load_pln", lambda: [{
        "id": "pln_spklu-1", "source": "pln_spklu", "latitude": -6.2, "longitude": 106.8,
        "name": "X", "address": None, "province": "DKI Jakarta", "city": None,
        "operator": "PLN", "power_kw": 150.0, "charge_type": "fast",
        "connectors": 1, "status": "operational", "date_verified": None,
    }])
    monkeypatch.setattr(sources, "_load_ocm", lambda: [])
    monkeypatch.setattr(sources, "_load_osm", lambda: [])
    rows = sources.normalized_rows()
    assert len(rows) == 1
    assert rows[0]["connector_types"] == ["CCS2"]
    assert rows[0]["speed_tier"] == "fast"


@pytest.mark.unit
def test_normalized_rows_nan_power_becomes_none(monkeypatch):
    monkeypatch.setattr(sources, "_load_pln", lambda: [{
        "id": "pln_spklu-2", "source": "pln_spklu", "latitude": -6.2, "longitude": 106.8,
        "name": None, "address": None, "province": None, "city": None, "operator": None,
        "power_kw": math.nan, "charge_type": None, "connectors": None,
        "status": None, "date_verified": None,
    }])
    monkeypatch.setattr(sources, "_load_ocm", lambda: [])
    monkeypatch.setattr(sources, "_load_osm", lambda: [])
    assert sources.normalized_rows()[0]["power_kw"] is None
```

- [ ] **Step 5: Run + confirm nothing else imports the removed `data.load`**

Run: `.venv/bin/python -m pytest tests/test_sources.py -v`
Expected: 2 passed.
Run: `grep -rn "data.load\|data.reload\|data._DF" api/ tests/`
Expected: only references inside `api/main.py`, `api/routing.py`, and the two integration test files (fixed in later tasks). Note them.

- [ ] **Step 6: Commit**

```bash
git add api/sources.py api/data.py tests/test_sources.py
git commit -m "refactor: extract source normalization into api/sources.py"
```

---

## Task 4: Alembic migration (PostGIS + stations table)

**Files:**
- Create: `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_stations.py`

- [ ] **Step 1: Init Alembic**

Run: `.venv/bin/alembic init alembic`
Expected: creates `alembic.ini` and `alembic/`.

- [ ] **Step 2: Point Alembic at `DATABASE_URL`.** In `alembic/env.py`, after the existing imports add:

```python
import os
config.set_main_option(
    "sqlalchemy.url",
    os.getenv("DATABASE_URL", "postgresql+psycopg://evflow:evflow@localhost:5432/evflow"),
)
```

- [ ] **Step 3: Replace the generated empty revision** with `alembic/versions/0001_stations.py`:

```python
"""stations table + postgis

Revision ID: 0001_stations
Revises:
Create Date: 2026-06-01
"""
from alembic import op

revision = "0001_stations"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
    op.execute("""
        CREATE TABLE stations (
            id                 text PRIMARY KEY,
            geom               geometry(Point, 4326) NOT NULL,
            name               text,
            address            text,
            province           text,
            city               text,
            operator           text,
            power_kw           double precision,
            speed_tier         text,
            connector_types    text[] NOT NULL DEFAULT '{}',
            connector_inferred boolean NOT NULL DEFAULT true,
            sources            text[] NOT NULL DEFAULT '{}',
            status             text,
            date_verified      text
        );
    """)
    op.execute("CREATE INDEX stations_geom_gix ON stations USING GIST (geom);")
    op.execute("CREATE INDEX stations_province_ix ON stations (province);")
    op.execute("CREATE INDEX stations_speed_tier_ix ON stations (speed_tier);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS stations;")
```

- [ ] **Step 4: Commit** (migration runs against a live DB later, in Task 8/10)

```bash
git add alembic.ini alembic/ && git commit -m "feat: alembic migration for postgis stations table"
```

---

## Task 5: Seed script

**Files:**
- Create: `scripts/seed_db.py`
- Test: `tests/test_seed_build.py`

- [ ] **Step 1: Write `scripts/seed_db.py`**

```python
"""Load + dedupe stations into Postgres. Run once after `alembic upgrade head`:

    python -m scripts.seed_db

Reads data/raw/*.json (host-mounted), normalizes, infers connectors, clusters
within 75 m, then truncates and inserts the unique stations.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text          # noqa: E402

from api import connectors, dedup, sources  # noqa: E402
from api.db import engine            # noqa: E402

_INSERT = text("""
    INSERT INTO stations
      (id, geom, name, address, province, city, operator, power_kw, speed_tier,
       connector_types, connector_inferred, sources, status, date_verified)
    VALUES
      (:id, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), :name, :address, :province,
       :city, :operator, :power_kw, :speed_tier, :connector_types, :connector_inferred,
       :sources, :status, :date_verified)
""")


def build_stations() -> list[dict]:
    merged = dedup.cluster_stations(sources.normalized_rows())
    for m in merged:
        # recompute speed tier from the merged (max) power
        m["speed_tier"] = connectors.speed_tier(m.get("power_kw"), m.get("charge_type"))
        m["connector_inferred"] = True
    return merged


def main() -> None:
    stations = build_stations()
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE stations;"))
        for s in stations:
            conn.execute(_INSERT, {
                "id": s["id"], "lat": s["latitude"], "lon": s["longitude"],
                "name": s.get("name"), "address": s.get("address"),
                "province": s.get("province"), "city": s.get("city"),
                "operator": s.get("operator"), "power_kw": s.get("power_kw"),
                "speed_tier": s.get("speed_tier"),
                "connector_types": list(s.get("connector_types") or []),
                "connector_inferred": bool(s.get("connector_inferred", True)),
                "sources": list(s.get("sources") or []),
                "status": s.get("status"), "date_verified": s.get("date_verified"),
            })
    print(f"seeded {len(stations)} stations")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write the test** `tests/test_seed_build.py` (covers `build_stations`, no DB):

```python
import pytest

from scripts import seed_db
from api import sources


@pytest.mark.unit
def test_build_stations_dedupes_and_sets_fields(monkeypatch):
    monkeypatch.setattr(sources, "normalized_rows", lambda: [
        {"id": "pln_spklu-1", "source": "pln_spklu", "latitude": -6.2000, "longitude": 106.8000,
         "name": "PLN", "address": None, "province": "DKI Jakarta", "city": None, "operator": "PLN",
         "power_kw": 22.0, "charge_type": "medium", "status": None, "date_verified": None,
         "connector_types": ["AC Type 2"]},
        {"id": "open_charge_map-9", "source": "open_charge_map", "latitude": -6.20015, "longitude": 106.8000,
         "name": "OCM", "address": None, "province": None, "city": None, "operator": None,
         "power_kw": 150.0, "charge_type": None, "status": None, "date_verified": None,
         "connector_types": ["CCS2"]},
    ])
    out = seed_db.build_stations()
    assert len(out) == 1
    s = out[0]
    assert s["id"] == "pln_spklu-1"
    assert sorted(s["sources"]) == ["open_charge_map", "pln_spklu"]
    assert s["power_kw"] == 150.0
    assert s["speed_tier"] == "fast"            # recomputed from 150 kW
    assert sorted(s["connector_types"]) == ["AC Type 2", "CCS2"]
```

(Create empty `scripts/__init__.py` so `from scripts import seed_db` imports.)

- [ ] **Step 3: Run**

Run: `.venv/bin/python -m pytest tests/test_seed_build.py -v`
Expected: 1 passed.

- [ ] **Step 4: Commit**

```bash
git add scripts/seed_db.py scripts/__init__.py tests/test_seed_build.py
git commit -m "feat: station seed script (normalize -> dedupe -> insert)"
```

---

## Task 6: Stations repository (SQL queries)

**Files:**
- Create: `api/stations_repo.py`

- [ ] **Step 1: Write `api/stations_repo.py`**

```python
"""All station SQL lives here, isolated from the endpoints. Functions return
plain dicts/tuples that map onto the Pydantic models in api/models.py.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import text

from .db import engine

# geom decomposed back to lat/lon; arrays come back as Python lists.
_COLS = """
    id, name, ST_Y(geom) AS latitude, ST_X(geom) AS longitude, address, province,
    city, operator, power_kw, speed_tier, connector_types, connector_inferred,
    sources, status, date_verified
"""


def _where(filters: dict) -> tuple[str, dict]:
    clauses, params = [], {}
    if filters.get("source"):
        clauses.append(":source = ANY(sources)"); params["source"] = filters["source"]
    if filters.get("connector_type"):
        clauses.append(":ct = ANY(connector_types)"); params["ct"] = filters["connector_type"]
    if filters.get("speed_tier"):
        clauses.append("speed_tier = :st"); params["st"] = filters["speed_tier"]
    if filters.get("province"):
        clauses.append("lower(province) = lower(:prov)"); params["prov"] = filters["province"]
    if filters.get("city"):
        clauses.append("city ILIKE :city"); params["city"] = f"%{filters['city']}%"
    if filters.get("q"):
        clauses.append("name ILIKE :q"); params["q"] = f"%{filters['q']}%"
    if filters.get("min_power") is not None:
        clauses.append("power_kw >= :minp"); params["minp"] = filters["min_power"]
    if filters.get("max_power") is not None:
        clauses.append("power_kw <= :maxp"); params["maxp"] = filters["max_power"]
    if filters.get("bbox"):
        mnlon, mnlat, mxlon, mxlat = filters["bbox"]
        clauses.append("geom && ST_MakeEnvelope(:mnlon,:mnlat,:mxlon,:mxlat,4326)")
        params.update(mnlon=mnlon, mnlat=mnlat, mxlon=mxlon, mxlat=mxlat)
    sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return sql, params


def list_stations(filters: dict, limit: int, offset: int) -> tuple[int, list[dict]]:
    where, params = _where(filters)
    with engine.connect() as c:
        total = c.execute(text(f"SELECT count(*) FROM stations{where}"), params).scalar_one()
        rows = c.execute(
            text(f"SELECT {_COLS} FROM stations{where} ORDER BY id LIMIT :lim OFFSET :off"),
            {**params, "lim": limit, "off": offset},
        ).mappings().all()
    return total, [dict(r) for r in rows]


def get_station(station_id: str) -> Optional[dict]:
    with engine.connect() as c:
        row = c.execute(text(f"SELECT {_COLS} FROM stations WHERE id = :id"),
                        {"id": station_id}).mappings().first()
    return dict(row) if row else None


def nearby(lat: float, lon: float, radius_km: float, limit: int,
           source: Optional[str] = None) -> list[dict]:
    src = " AND :source = ANY(sources)" if source else ""
    params = {"lat": lat, "lon": lon, "r": radius_km * 1000.0, "lim": limit}
    if source:
        params["source"] = source
    sql = f"""
        SELECT {_COLS},
               ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography)/1000.0 AS distance_km
        FROM stations
        WHERE ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography, :r){src}
        ORDER BY distance_km ASC
        LIMIT :lim
    """
    with engine.connect() as c:
        return [dict(r) for r in c.execute(text(sql), params).mappings().all()]


def count() -> int:
    with engine.connect() as c:
        return c.execute(text("SELECT count(*) FROM stations")).scalar_one()


def source_counts() -> list[tuple[str, int]]:
    sql = "SELECT unnest(sources) AS s, count(*) FROM stations GROUP BY s ORDER BY count(*) DESC"
    with engine.connect() as c:
        return [(r[0], r[1]) for r in c.execute(text(sql)).all()]


def connector_counts() -> list[tuple[str, int]]:
    sql = ("SELECT unnest(connector_types) AS t, count(*) FROM stations "
           "GROUP BY t ORDER BY count(*) DESC")
    with engine.connect() as c:
        return [(r[0], r[1]) for r in c.execute(text(sql)).all()]


def speed_tier_counts() -> dict[str, int]:
    sql = "SELECT speed_tier, count(*) FROM stations WHERE speed_tier IS NOT NULL GROUP BY speed_tier"
    with engine.connect() as c:
        return {r[0]: r[1] for r in c.execute(text(sql)).all()}


def provinces() -> list[tuple[str, int]]:
    sql = ("SELECT province, count(*) FROM stations WHERE province IS NOT NULL "
           "GROUP BY province ORDER BY count(*) DESC")
    with engine.connect() as c:
        return [(r[0], r[1]) for r in c.execute(text(sql)).all()]


def cities(province: Optional[str]) -> list[tuple[str, int]]:
    where = "WHERE city IS NOT NULL"
    params = {}
    if province:
        where += " AND lower(province) = lower(:prov)"; params["prov"] = province
    sql = f"SELECT city, count(*) FROM stations {where} GROUP BY city ORDER BY count(*) DESC"
    with engine.connect() as c:
        return [(r[0], r[1]) for r in c.execute(text(sql), params).all()]


def stats() -> dict:
    with engine.connect() as c:
        total = c.execute(text("SELECT count(*) FROM stations")).scalar_one()
        p = c.execute(text(
            "SELECT count(power_kw), min(power_kw), max(power_kw), round(avg(power_kw)::numeric,2) "
            "FROM stations")).one()
    return {"total": total, "with_power_kw": p[0],
            "power_kw_min": float(p[1]) if p[1] is not None else None,
            "power_kw_max": float(p[2]) if p[2] is not None else None,
            "power_kw_mean": float(p[3]) if p[3] is not None else None}


def routing_coords(source: Optional[str] = None) -> list[dict]:
    """id + lat/lon for every station, for the routing nearest-station scan."""
    where = " WHERE :source = ANY(sources)" if source else ""
    params = {"source": source} if source else {}
    sql = f"SELECT id, ST_Y(geom) AS latitude, ST_X(geom) AS longitude FROM stations{where}"
    with engine.connect() as c:
        return [dict(r) for r in c.execute(text(sql), params).mappings().all()]
```

- [ ] **Step 2: Commit** (this module is exercised by the DB integration tests in Task 9)

```bash
git add api/stations_repo.py
git commit -m "feat: stations repository (PostGIS-backed queries)"
```

---

## Task 7: Models update (source -> sources)

**Files:**
- Modify: `api/models.py`

- [ ] **Step 1: Change the `Station` model.** Replace the `source: Source` line with a `sources` list, and keep the `Source` enum (still used by `SourceCount` and the `?source=` filter):

```python
    # was: source: Source = Field(..., description="Originating dataset.")
    sources: list[Source] = Field(
        default_factory=list,
        description="Datasets this station appears in (deduplicated).",
        examples=[["pln_spklu", "open_charge_map"]])
```

- [ ] **Step 2: Commit**

```bash
git add api/models.py
git commit -m "feat: Station.source -> Station.sources (dedup)"
```

---

## Task 8: Wire endpoints to the repository

**Files:**
- Modify: `api/main.py`, `api/routing.py`

- [ ] **Step 1: Update imports + lifespan in `api/main.py`.** Replace `from . import __version__, data, evmodels` with:

```python
from . import __version__, evmodels
from . import connectors as conn
from . import stations_repo as repo
```

Replace the `lifespan` body (which called `data.load()`) with a no-op warmup that tolerates a not-yet-seeded DB:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
```

- [ ] **Step 2: Replace `_row_to_station` + `_apply_filters`** in `api/main.py`. The row is now a repo dict, not a pandas Series, so:

```python
def _row_to_station(row: dict, distance_km: Optional[float] = None) -> Station:
    return Station(
        id=row["id"], name=row.get("name"), sources=row.get("sources") or [],
        latitude=float(row["latitude"]), longitude=float(row["longitude"]),
        address=row.get("address"), province=row.get("province"), city=row.get("city"),
        operator=row.get("operator"), power_kw=row.get("power_kw"),
        charge_type=row.get("charge_type"), speed_tier=row.get("speed_tier"),
        connectors=None, connector_types=row.get("connector_types") or [],
        connector_inferred=row.get("connector_inferred"),
        status=row.get("status"), date_verified=row.get("date_verified"),
        distance_km=(round(distance_km, 3) if distance_km is not None else
                     (round(row["distance_km"], 3) if row.get("distance_km") is not None else None)),
    )


def _bbox(bbox: Optional[str]):
    if not bbox:
        return None
    try:
        mnlon, mnlat, mxlon, mxlat = (float(x) for x in bbox.split(","))
    except ValueError:
        raise HTTPException(422, "bbox must be 'minLon,minLat,maxLon,maxLat'")
    return (mnlon, mnlat, mxlon, mxlat)
```

(Delete the old pandas `_apply_filters` and the `_clean` helper. `numpy`/`pandas` imports in `main.py` can be removed.)

- [ ] **Step 2b: Note on `connectors` field.** The deduped schema does not keep a single connector *count*, so `Station.connectors` is always `None` now. Leave the field in the model for compatibility; the count is no longer meaningful after merging.

- [ ] **Step 3: Rewrite the station endpoints** in `api/main.py`:

```python
@app.get("/api/v1/stations", response_model=StationList, tags=["stations"],
         summary="List / filter charging stations")
def list_stations(
    source: Optional[Source] = Query(None),
    province: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    min_power: Optional[float] = Query(None, ge=0),
    max_power: Optional[float] = Query(None, ge=0),
    connector_type: Optional[str] = Query(None, examples=["CCS2"]),
    speed_tier: Optional[str] = Query(None),
    bbox: Optional[str] = Query(None, examples=["106.55,-6.65,107.10,-5.95"]),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> StationList:
    filters = {"source": source.value if source else None, "province": province,
               "city": city, "q": q, "min_power": min_power, "max_power": max_power,
               "connector_type": connector_type, "speed_tier": speed_tier, "bbox": _bbox(bbox)}
    total, rows = repo.list_stations(filters, limit, offset)
    return StationList(total=total, limit=limit, offset=offset,
                       items=[_row_to_station(r) for r in rows])


@app.get("/api/v1/stations/nearby", response_model=list[Station], tags=["stations"],
         summary="Nearest stations to a point ('near me')")
def nearby(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
           radius_km: float = Query(5.0, gt=0, le=500), limit: int = Query(20, ge=1, le=200),
           source: Optional[Source] = Query(None)) -> list[Station]:
    rows = repo.nearby(lat, lon, radius_km, limit, source.value if source else None)
    return [_row_to_station(r) for r in rows]


@app.get("/api/v1/stations/{station_id}", response_model=Station, tags=["stations"],
         summary="Fetch one station by id", responses={404: {"description": "Not found"}})
def get_station(station_id: str) -> Station:
    row = repo.get_station(station_id)
    if row is None:
        raise HTTPException(404, f"station '{station_id}' not found")
    return _row_to_station(row)
```

- [ ] **Step 4: Rewrite geojson + meta endpoints** in `api/main.py`:

```python
@app.get("/api/v1/stations.geojson", response_model=GeoJSONFeatureCollection, tags=["geo"],
         summary="Stations as a GeoJSON FeatureCollection")
def stations_geojson(
    source: Optional[Source] = Query(None), province: Optional[str] = Query(None),
    city: Optional[str] = Query(None), q: Optional[str] = Query(None),
    min_power: Optional[float] = Query(None, ge=0), max_power: Optional[float] = Query(None, ge=0),
    connector_type: Optional[str] = Query(None), speed_tier: Optional[str] = Query(None),
    bbox: Optional[str] = Query(None), limit: int = Query(5000, ge=1, le=20000),
) -> GeoJSONFeatureCollection:
    filters = {"source": source.value if source else None, "province": province, "city": city,
               "q": q, "min_power": min_power, "max_power": max_power,
               "connector_type": connector_type, "speed_tier": speed_tier, "bbox": _bbox(bbox)}
    _, rows = repo.list_stations(filters, limit, 0)
    features = []
    for r in rows:
        st = _row_to_station(r)
        props = st.model_dump(exclude={"latitude", "longitude", "distance_km"})
        features.append({"type": "Feature",
                         "geometry": {"type": "Point", "coordinates": [float(r["longitude"]), float(r["latitude"])]},
                         "properties": props})
    return GeoJSONFeatureCollection(type="FeatureCollection", features=features)


@app.get("/api/v1/stats", response_model=Stats, tags=["meta"], summary="Aggregate statistics")
def stats() -> Stats:
    s = repo.stats()
    by_source = [SourceCount(source=src, count=c) for src, c in repo.source_counts()]
    by_prov = [NameCount(name=n, count=c) for n, c in repo.provinces()[:40]]
    by_type = [NameCount(name=n, count=c) for n, c in repo.connector_counts()]
    return Stats(total=s["total"], by_source=by_source, by_province=by_prov,
                 by_charge_type=by_type, with_power_kw=s["with_power_kw"],
                 power_kw_min=s["power_kw_min"], power_kw_max=s["power_kw_max"],
                 power_kw_mean=s["power_kw_mean"])


@app.get("/api/v1/sources", response_model=list[SourceCount], tags=["meta"])
def sources_lookup() -> list[SourceCount]:
    return [SourceCount(source=s, count=c) for s, c in repo.source_counts()]


@app.get("/api/v1/provinces", response_model=list[NameCount], tags=["meta"])
def provinces_lookup() -> list[NameCount]:
    return [NameCount(name=n, count=c) for n, c in repo.provinces()]


@app.get("/api/v1/cities", response_model=list[NameCount], tags=["meta"])
def cities_lookup(province: Optional[str] = Query(None)) -> list[NameCount]:
    return [NameCount(name=n, count=c) for n, c in repo.cities(province)]


@app.get("/api/v1/connectors", response_model=list[NameCount], tags=["meta"],
         summary="Connector types with counts for the filter dropdown (inferred)")
def connectors_lookup() -> list[NameCount]:
    return [NameCount(name=n, count=c) for n, c in repo.connector_counts()]


@app.get("/api/v1/speed-tiers", response_model=list[SpeedTier], tags=["meta"],
         summary="Speed tier definitions with counts")
def speed_tiers_lookup() -> list[SpeedTier]:
    counts = repo.speed_tier_counts()
    return [SpeedTier(id=t["id"], label=t["label"], min_kw=t["min_kw"], max_kw=t["max_kw"],
                      count=counts.get(t["id"], 0)) for t in conn.SPEED_TIERS]
```

- [ ] **Step 5: Update `/health`** in `api/main.py`:

```python
@app.get("/health", response_model=Health, tags=["system"], summary="Liveness + dataset size")
def health() -> Health:
    try:
        n = repo.count()
    except Exception:
        n = 0
    return Health(status="ok", stations_loaded=n, version=__version__)
```

- [ ] **Step 6: Update routing endpoints** in `api/main.py` (the `/route` and `/route/nearest-station` handlers) to get coordinates from the repo instead of `data.load()`:

In `/route`, replace the `station_id` lookup block:

```python
    if station_id:
        row = repo.get_station(station_id)
        if row is None:
            raise HTTPException(404, f"station '{station_id}' not found")
        dest_lat, dest_lon = float(row["latitude"]), float(row["longitude"])
    elif dest_lat is None or dest_lon is None:
        raise HTTPException(422, "provide either 'station_id' or both 'dest_lat' and 'dest_lon'")
```

In `/route/nearest-station`, replace the `data.load()` / DataFrame block with:

```python
    coords = repo.routing_coords(source.value if source else None)
    if not coords:
        raise HTTPException(404, "no charging stations loaded")
    # ev model range derivation stays unchanged ...
    from . import routing
    try:
        result = routing.nearest_station_route(
            lat, lon,
            [c["id"] for c in coords], [c["latitude"] for c in coords], [c["longitude"] for c in coords],
            weight=weight, max_range_km=range_used)
    except routing.GraphUnavailable as e:
        raise HTTPException(503, f"routing unavailable: {e}")
    if result is None:
        raise HTTPException(404, "no charging station reachable by road from this point")
    row = repo.get_station(result["station_id"])
    return NearestStationRoute(
        station=_row_to_station(row, distance_km=result["route"]["distance_m"] / 1000.0),
        route=result["route"], candidates_considered=result["candidates_considered"],
        within_range=result["within_range"], range_used_km=range_used)
```

- [ ] **Step 7: Compile-check**

Run: `.venv/bin/python -m py_compile api/main.py api/routing.py api/models.py api/sources.py api/stations_repo.py api/db.py api/dedup.py`
Expected: no output (success).

- [ ] **Step 8: Commit**

```bash
git add api/main.py api/routing.py
git commit -m "feat: serve station endpoints from PostgreSQL via the repo"
```

---

## Task 9: Tests (fix in-memory tests + add DB-gated integration)

**Files:**
- Modify: `tests/test_connector_endpoints.py`, `tests/test_route_endpoint.py`
- Create: `tests/conftest.py`, `tests/test_stations_db.py`

- [ ] **Step 1: Add a DB gate** in `tests/conftest.py`:

```python
import os
import pytest

requires_db = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping DB-backed tests",
)
```

- [ ] **Step 2: Replace the in-memory injection tests.** In `tests/test_connector_endpoints.py` and `tests/test_route_endpoint.py`, the fixtures that did `monkeypatch.setattr(data, "_DF", ...)` or patched `data._load_*` no longer apply (the in-memory path is gone). Delete those two files' DB-dependent fixtures/tests and move that coverage to `tests/test_stations_db.py` (next step). Keep any test in `test_route_endpoint.py` that only exercises pure dijkstra via `routing` with a synthetic graph and does **not** need station rows.

- [ ] **Step 3: Write `tests/test_stations_db.py`** (runs only when `DATABASE_URL` is set, against a seeded DB):

```python
import pytest

from tests.conftest import requires_db

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient   # noqa: E402


@requires_db
def test_stations_and_lookups_served_from_db():
    from api import main
    with TestClient(main.app) as c:
        assert c.get("/health").json()["stations_loaded"] > 0
        body = c.get("/api/v1/stations?limit=2").json()
        assert body["total"] > 0
        assert "sources" in body["items"][0]
        assert isinstance(body["items"][0]["sources"], list)
        assert c.get("/api/v1/connectors").status_code == 200
        tiers = {t["id"] for t in c.get("/api/v1/speed-tiers").json()}
        assert tiers == {"slow", "medium", "fast", "ultra_fast"}
        near = c.get("/api/v1/stations/nearby?lat=-6.2088&lon=106.8456&radius_km=5&limit=3").json()
        assert all("distance_km" in s for s in near)
```

- [ ] **Step 4: Run the full suite without a DB** (DB tests skip)

Run: `.venv/bin/python -m pytest -q`
Expected: all pass; `test_stations_db.py` reports `skipped`.

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test: dedup unit tests + DB-gated station endpoint tests"
```

---

## Task 10: Deployment + docs + spec regen

**Files:**
- Modify: `podman-compose.yml`, `.env.deploy.example`, `DEPLOY.md`, `FRONTEND_API.md`, `openapi.json`, `openapi.yaml`

- [ ] **Step 1: Add the `db` service** to `podman-compose.yml` and a `DATABASE_URL` to the api service:

```yaml
services:
  db:
    image: docker.io/postgis/postgis:16-3.4
    container_name: ev-flow-db
    restart: unless-stopped
    network_mode: host
    environment:
      POSTGRES_USER: evflow
      POSTGRES_PASSWORD: evflow
      POSTGRES_DB: evflow
    volumes:
      - pgdata:/var/lib/postgresql/data:Z

  api:
    # ...existing config...
    environment:
      WEB_CONCURRENCY: "${WEB_CONCURRENCY:-2}"
      CORS_ALLOW_ORIGINS: "${CORS_ALLOW_ORIGINS:-*}"
      DATABASE_URL: "postgresql+psycopg://evflow:evflow@localhost:5432/evflow"

volumes:
  pgdata:
```

- [ ] **Step 2: Document the start/seed order** in `DEPLOY.md` (replace the deploy steps):

```bash
podman compose up -d --build db api
podman compose exec api alembic upgrade head        # create the schema
podman compose exec api python -m scripts.seed_db   # load + dedupe stations
curl -s http://localhost:8000/health                # stations_loaded ~1147
```

Add a security note: **keep port 5432 closed to the public** (only the API is exposed via the Cloudflare Tunnel; Postgres is reachable only on the host).

- [ ] **Step 3: Update `FRONTEND_API.md`** for `source` -> `sources`: the station JSON now has `"sources": ["pln_spklu", ...]` instead of `"source": "..."`, and the `?source=` filter means "stations that include this source". Update the example response bodies and the field-coverage table.

- [ ] **Step 4: Regenerate the spec** (requires the venv, no DB needed for `app.openapi()`):

Run: `.venv/bin/python -m api.export_openapi`
Expected: writes `openapi.json` (15 paths) + `openapi.yaml`; the `Station` schema now shows `sources`.

- [ ] **Step 5: Commit**

```bash
git add podman-compose.yml .env.deploy.example DEPLOY.md FRONTEND_API.md openapi.json openapi.yaml
git commit -m "feat: add postgis db service + deploy/seed docs; sources in contract"
```

---

## Task 11: End-to-end verification against a real database

**Files:** none (verification only)

- [ ] **Step 1: Start a local Postgres** (matches the deploy):

Run: `podman run -d --name evflow-db-test -p 5432:5432 -e POSTGRES_USER=evflow -e POSTGRES_PASSWORD=evflow -e POSTGRES_DB=evflow docker.io/postgis/postgis:16-3.4`
Expected: container starts.

- [ ] **Step 2: Migrate + seed** (data/raw present locally)

Run:
```bash
export DATABASE_URL="postgresql+psycopg://evflow:evflow@localhost:5432/evflow"
.venv/bin/alembic upgrade head
.venv/bin/python -m scripts.seed_db
```
Expected: `seeded ~1147 stations`.

- [ ] **Step 3: Run the full suite with the DB**

Run: `DATABASE_URL="postgresql+psycopg://evflow:evflow@localhost:5432/evflow" .venv/bin/python -m pytest -q`
Expected: all pass, including `test_stations_db.py`.

- [ ] **Step 4: Smoke the endpoints**

Run: `DATABASE_URL=... .venv/bin/uvicorn api.main:app --port 8009 &` then `bash scripts/smoke_test.sh http://127.0.0.1:8009`
Expected: station endpoints return real data; `/health` shows ~1147; `/nearby` returns `distance_km`.

- [ ] **Step 5: Tear down the test DB**

Run: `podman rm -f evflow-db-test`

---

## Self-review checklist (run before handing off)

- **Spec coverage:** PostGIS engine (Task 1,4,10) · dedup to ~1147 (Task 2,5) · stations in DB served live (Task 6,8) · PostGIS `/nearby` (Task 6) · inferred connector/speed stored (Task 5) · `source`->`sources` contract (Task 7,8,10) · Alembic (Task 4) · compose db + deploy order + 5432 closed (Task 10) · dedup pure unit tests + DB-gated integration (Task 2,9) · routing reads coords from DB (Task 8). All present.
- **Placeholder scan:** none.
- **Type/name consistency:** `cluster_stations`, `normalized_rows`, `build_stations`, `repo.*` names used identically across tasks; `sources` list used in model + repo + seed + tests.
