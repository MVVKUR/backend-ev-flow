# Per-connector Enrichment (OCM level) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose a per-connector list (`connectors: [{type, count, speed_tier, power_kw, type_inferred}]`) on each station, keeping OCM's real count/power/speed and inferring only the type, without breaking existing fields, filters, or endpoints.

**Architecture:** Stop collapsing OCM `Connections`. New pure helpers in `api/connectors.py` build and merge per-connector lists; the OCM loader emits raw per-connection data; `normalized_rows` and `dedup` produce a `connectors` list and re-derive the existing station-level fields from it; a JSONB column stores it; the API and seeder pass it through. Tests mock sources/DB.

**Tech Stack:** FastAPI, SQLAlchemy 2 (psycopg), Alembic, PostgreSQL JSONB, Pydantic v2, pytest.

**Spec:** `docs/superpowers/specs/2026-06-07-connector-enrichment-design.md`

---

## File structure

| File | Responsibility | New/Modify |
|---|---|---|
| `api/connectors.py` | add `build_connectors`, `merge_connectors`, `derive_station_fields` | Modify |
| `api/sources.py` | OCM loader emits `_connections`; `normalized_rows` builds `connectors` + derived fields | Modify |
| `api/dedup.py` | merge connector lists across a cluster + re-derive | Modify |
| `alembic/versions/0003_station_connectors.py` | `connectors jsonb` column | Create |
| `api/models.py` | `Connector` model; `Station.connectors: list[Connector]` | Modify |
| `scripts/seed_db.py` | insert `connectors` jsonb; simplify `build_stations` | Modify |
| `api/stations_repo.py` | add `connectors` to the SELECT columns | Modify |
| `api/main.py` | `_row_to_station` passes `connectors` | Modify |
| `tests/test_connectors.py`, `test_sources.py`, `test_dedup.py`, `test_seed_build.py`, `test_stations_db.py` | unit + DB-gated | Modify |
| `openapi.json` / `openapi.yaml` | regen | Modify |

---

## Task 1: connector list helpers (pure)

**Files:** Modify `api/connectors.py`; Modify `tests/test_connectors.py`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_connectors.py`):
```python
@pytest.mark.unit
def test_build_connectors_aggregates_by_type_and_power():
    out = conn.build_connectors([
        {"power_kw": 200, "count": 1},
        {"power_kw": 200, "count": 1},   # same (CCS2, 200) -> counts add to 2
        {"power_kw": 7, "count": 1},     # (AC Type 2, 7)
    ])
    assert out == [
        {"type": "CCS2", "count": 2, "speed_tier": "ultra_fast", "power_kw": 200, "type_inferred": True},
        {"type": "AC Type 2", "count": 1, "speed_tier": "slow", "power_kw": 7, "type_inferred": True},
    ]


@pytest.mark.unit
def test_build_connectors_skips_unknown_and_nan():
    import math
    assert conn.build_connectors([{"power_kw": None, "count": 1},
                                  {"power_kw": math.nan, "count": 2}]) == []


@pytest.mark.unit
def test_build_connectors_uses_charge_type_when_power_missing():
    out = conn.build_connectors([{"power_kw": None, "count": 2}], charge_type="fast")
    assert out == [{"type": "CCS2", "count": 2, "speed_tier": "fast",
                    "power_kw": None, "type_inferred": True}]


@pytest.mark.unit
def test_merge_connectors_takes_max_count_not_sum():
    a = [{"type": "CCS2", "count": 2, "speed_tier": "fast", "power_kw": 150, "type_inferred": True}]
    b = [{"type": "CCS2", "count": 2, "speed_tier": "fast", "power_kw": 150, "type_inferred": True},
         {"type": "AC Type 2", "count": 1, "speed_tier": "slow", "power_kw": 7, "type_inferred": True}]
    out = conn.merge_connectors([a, b])
    assert {c["type"]: c["count"] for c in out} == {"CCS2": 2, "AC Type 2": 1}


@pytest.mark.unit
def test_derive_station_fields():
    conns = [{"type": "AC Type 2", "count": 1, "speed_tier": "slow", "power_kw": 7, "type_inferred": True},
             {"type": "CCS2", "count": 2, "speed_tier": "ultra_fast", "power_kw": 200, "type_inferred": True}]
    assert conn.derive_station_fields(conns) == {
        "connector_types": ["AC Type 2", "CCS2"], "power_kw": 200,
        "speed_tier": "ultra_fast", "connector_inferred": True}
    assert conn.derive_station_fields([]) == {
        "connector_types": [], "power_kw": None, "speed_tier": None, "connector_inferred": True}
```

- [ ] **Step 2: Run, expect failure** `.venv/bin/python -m pytest tests/test_connectors.py -q` -> FAIL (`build_connectors` not defined).

- [ ] **Step 3: Implement.** In `api/connectors.py` add `import math` to the imports, and append:
```python
def build_connectors(connections: list[dict], charge_type: Optional[str] = None) -> list[dict]:
    """Build an aggregated connector list from raw per-connection rows.

    `connections` items are dicts with `power_kw` (float|None, NaN treated as None)
    and `count` (int). Entries sharing the same (inferred type, power_kw) are merged
    and their counts summed. Connections whose type cannot be inferred are dropped.
    """
    agg: dict = {}
    order: list = []
    for c in connections:
        p = c.get("power_kw")
        if isinstance(p, float) and math.isnan(p):
            p = None
        cnt = c.get("count") or 1
        types = infer_connectors(p, charge_type)
        if not types:
            continue
        key = (types[0], p)
        if key not in agg:
            agg[key] = {"type": types[0], "count": 0, "speed_tier": speed_tier(p, charge_type),
                        "power_kw": p, "type_inferred": True}
            order.append(key)
        agg[key]["count"] += cnt
    return [agg[k] for k in order]


def merge_connectors(lists: list[list[dict]]) -> list[dict]:
    """Merge several connector lists, grouping by (type, power_kw), count = MAX.

    Used when dedup clusters points from multiple sources that describe the same
    physical station: taking the max avoids double-counting shared connectors while
    keeping the union of all known types.
    """
    agg: dict = {}
    order: list = []
    for lst in lists:
        for c in (lst or []):
            key = (c["type"], c.get("power_kw"))
            if key not in agg:
                agg[key] = dict(c)
                order.append(key)
            else:
                agg[key]["count"] = max(agg[key].get("count", 1), c.get("count", 1))
    return [agg[k] for k in order]


def derive_station_fields(conns: list[dict]) -> dict:
    """Derive station-level connector_types, power_kw, speed_tier, connector_inferred."""
    if not conns:
        return {"connector_types": [], "power_kw": None, "speed_tier": None, "connector_inferred": True}
    powered = [c for c in conns if c.get("power_kw") is not None]
    top = max(powered, key=lambda c: c["power_kw"]) if powered else conns[0]
    return {
        "connector_types": sorted({c["type"] for c in conns}),
        "power_kw": top.get("power_kw"),
        "speed_tier": top.get("speed_tier"),
        "connector_inferred": any(c.get("type_inferred") for c in conns),
    }
```

- [ ] **Step 4: Run, expect pass** `.venv/bin/python -m pytest tests/test_connectors.py -q` -> all pass.

- [ ] **Step 5: Commit**
```bash
git add api/connectors.py tests/test_connectors.py
git commit -m "feat: connector list helpers (build/merge/derive)"
```

---

## Task 2: OCM per-connection loader + normalized_rows

**Files:** Modify `api/sources.py`; Modify `tests/test_sources.py`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_sources.py`):
```python
@pytest.mark.unit
def test_normalized_rows_ocm_builds_per_connector_list(monkeypatch):
    monkeypatch.setattr(sources, "_load_pln", lambda: [])
    monkeypatch.setattr(sources, "_load_ocm", lambda: [{
        "id": "open_charge_map-1", "source": "open_charge_map", "latitude": -6.2, "longitude": 106.8,
        "name": "OCM", "address": None, "province": None, "city": None, "operator": None,
        "power_kw": 200.0, "charge_type": None, "connectors": 3, "status": None, "date_verified": None,
        "_connections": [{"power_kw": 200.0, "count": 2}, {"power_kw": 7.0, "count": 1}],
    }])
    monkeypatch.setattr(sources, "_load_osm", lambda: [])
    row = sources.normalized_rows()[0]
    assert {c["type"]: c["count"] for c in row["connectors"]} == {"CCS2": 2, "AC Type 2": 1}
    assert row["connector_types"] == ["AC Type 2", "CCS2"]
    assert row["power_kw"] == 200.0
    assert row["speed_tier"] == "ultra_fast"
```
(The two existing `test_normalized_rows_*` tests keep passing via the fallback path below: they supply `power_kw` + `connectors` count but no `_connections`.)

- [ ] **Step 2: Run, expect failure** `.venv/bin/python -m pytest tests/test_sources.py -q` -> the new test FAILS (`connectors` is still the int 3, not a list).

- [ ] **Step 3: Implement.** In `api/sources.py`:

(a) In `_load_ocm`, add a `_connections` key to the emitted dict (keep all existing keys). Find the `out.append({...})` in `_load_ocm` and add this line inside the dict (e.g. right after `"power_kw": max(power) if power else math.nan,`):
```python
            "_connections": [{"power_kw": c.get("PowerKW"), "count": c.get("Quantity") or 1}
                             for c in conns],
```

(b) Replace the body of `normalized_rows` with:
```python
def normalized_rows() -> list[dict]:
    """All source rows, normalized, each with a `connectors` list + derived fields."""
    rows = _load_pln() + _load_ocm() + _load_osm()
    out = []
    for r in rows:
        if r.get("latitude") is None or r.get("longitude") is None:
            continue
        conns_in = r.get("_connections")
        if conns_in is None:  # PLN/OSM (and tests): one connection from station power + count
            conns_in = [{"power_kw": _clean_power(r.get("power_kw")), "count": r.get("connectors") or 1}]
        r["connectors"] = connectors.build_connectors(conns_in, r.get("charge_type"))
        derived = connectors.derive_station_fields(r["connectors"])
        r["connector_types"] = derived["connector_types"]
        r["speed_tier"] = derived["speed_tier"]
        r["power_kw"] = derived["power_kw"]
        r["connector_inferred"] = derived["connector_inferred"]
        out.append(r)
    return out
```

- [ ] **Step 4: Run, expect pass** `.venv/bin/python -m pytest tests/test_sources.py -q` -> all pass (new + the two existing).

- [ ] **Step 5: Commit**
```bash
git add api/sources.py tests/test_sources.py
git commit -m "feat: OCM per-connection extraction + connectors in normalized_rows"
```

---

## Task 3: dedup merges connector lists

**Files:** Modify `api/dedup.py`; Modify `tests/test_dedup.py`.

- [ ] **Step 1: Update the tests.** Replace the `_row` helper and the merge test in `tests/test_dedup.py` (the other three tests stay as-is; they call `_row` without connectors):
```python
def _row(id, source, lat, lon, power=None, name=None, conns=None):
    return {"id": id, "source": source, "latitude": lat, "longitude": lon,
            "power_kw": power, "name": name, "address": None, "province": None,
            "city": None, "operator": None, "charge_type": None, "status": None,
            "date_verified": None, "connectors": conns or []}


@pytest.mark.unit
def test_two_points_within_75m_merge():
    a = _row("pln_spklu-1", "pln_spklu", -6.2000, 106.8000,
             conns=[{"type": "AC Type 2", "count": 1, "speed_tier": "medium",
                     "power_kw": 22, "type_inferred": True}])
    b = _row("open_charge_map-9", "open_charge_map", -6.20020, 106.8000,
             conns=[{"type": "CCS2", "count": 2, "speed_tier": "fast",
                     "power_kw": 150, "type_inferred": True}])
    out = cluster_stations([a, b])
    assert len(out) == 1
    s = out[0]
    assert s["id"] == "pln_spklu-1"
    assert sorted(s["sources"]) == ["open_charge_map", "pln_spklu"]
    assert s["power_kw"] == 150
    assert sorted(s["connector_types"]) == ["AC Type 2", "CCS2"]
    assert {c["type"] for c in s["connectors"]} == {"AC Type 2", "CCS2"}
```

- [ ] **Step 2: Run, expect failure** `.venv/bin/python -m pytest tests/test_dedup.py -q` -> FAIL (old dedup ignores `connectors`, `s["connectors"]` missing / wrong).

- [ ] **Step 3: Implement.** In `api/dedup.py`:

(a) Add the import near the top (after the module docstring imports):
```python
from . import connectors
```

(b) Replace `_new_cluster`, `_merge_into`, and `_finalize` with:
```python
def _new_cluster(r: dict) -> dict:
    c = dict(r)
    c["sources"] = [r["source"]]
    c["_conn_lists"] = [r.get("connectors") or []]
    return c


def _merge_into(c: dict, r: dict) -> None:
    if r["source"] not in c["sources"]:
        c["sources"].append(r["source"])
    for f in _DESC_FIELDS:
        if not c.get(f) and r.get(f):
            c[f] = r[f]
    c["_conn_lists"].append(r.get("connectors") or [])


def _finalize(c: dict) -> None:
    merged = connectors.merge_connectors(c.pop("_conn_lists"))
    c["connectors"] = merged
    c.update(connectors.derive_station_fields(merged))
```

- [ ] **Step 4: Run, expect pass** `.venv/bin/python -m pytest tests/test_dedup.py -q` -> all pass.

- [ ] **Step 5: Commit**
```bash
git add api/dedup.py tests/test_dedup.py
git commit -m "feat: dedup merges per-connector lists (max count, union types)"
```

---

## Task 4: migration (connectors jsonb)

**Files:** Create `alembic/versions/0003_station_connectors.py`.

- [ ] **Step 1: Write the migration:**
```python
"""station connectors jsonb

Revision ID: 0003_station_connectors
Revises: 0002_wallet_topups
Create Date: 2026-06-07
"""
from alembic import op

revision = "0003_station_connectors"
down_revision = "0002_wallet_topups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE stations ADD COLUMN connectors jsonb NOT NULL DEFAULT '[]';")


def downgrade() -> None:
    op.execute("ALTER TABLE stations DROP COLUMN IF EXISTS connectors;")
```

- [ ] **Step 2: Sanity** `.venv/bin/python -c "import ast; ast.parse(open('alembic/versions/0003_station_connectors.py').read()); print('ok')"` and `.venv/bin/alembic history` shows `0002_wallet_topups -> 0003_station_connectors (head)`.

- [ ] **Step 3: Commit**
```bash
git add alembic/versions/0003_station_connectors.py
git commit -m "feat: migration for stations.connectors jsonb"
```

---

## Task 5: models (Connector + Station.connectors)

**Files:** Modify `api/models.py`.

- [ ] **Step 1: Add the `Connector` model** immediately before `class Station(BaseModel):` in `api/models.py`:
```python
class Connector(BaseModel):
    type: str = Field(..., description="Connector standard (inferred from power).", examples=["CCS2"])
    count: int = Field(..., description="Number of this connector at the station.", examples=[2])
    speed_tier: str = Field(..., examples=["ultra_fast"])
    power_kw: Optional[float] = Field(None, examples=[200.0])
    type_inferred: bool = Field(True, description="True when the type is inferred from power, not source data.")
```

- [ ] **Step 2: Replace the `connectors: Optional[int] = ...` line** in `class Station` with:
```python
    connectors: list[Connector] = Field(
        default_factory=list,
        description="Per-connector breakdown: type (inferred), real count/power/speed.")
```
(Leave `connector_types`, `speed_tier`, `power_kw`, `connector_inferred` as they are.)

- [ ] **Step 3: Verify** `.venv/bin/python -c "from api.models import Connector, Station; Station(id='x', latitude=0, longitude=0, connectors=[{'type':'CCS2','count':2,'speed_tier':'ultra_fast','power_kw':200,'type_inferred':True}]); print('ok')"` -> `ok`.

- [ ] **Step 4: Run no-DB suite** `.venv/bin/python -m pytest -q` -> green / DB tests skipped.

- [ ] **Step 5: Commit**
```bash
git add api/models.py
git commit -m "feat: Connector model + Station.connectors list"
```

---

## Task 6: seeder inserts connectors

**Files:** Modify `scripts/seed_db.py`; Modify `tests/test_seed_build.py`.

- [ ] **Step 1: Update the test.** Replace `test_build_stations_dedupes_and_sets_fields` in `tests/test_seed_build.py` with (rows now carry `connectors` lists):
```python
@pytest.mark.unit
def test_build_stations_dedupes_and_sets_fields(monkeypatch):
    monkeypatch.setattr(sources, "normalized_rows", lambda: [
        {"id": "pln_spklu-1", "source": "pln_spklu", "latitude": -6.2000, "longitude": 106.8000,
         "name": "PLN", "address": None, "province": "DKI Jakarta", "city": None, "operator": "PLN",
         "charge_type": "medium", "status": None, "date_verified": None,
         "connectors": [{"type": "AC Type 2", "count": 1, "speed_tier": "medium",
                         "power_kw": 22.0, "type_inferred": True}]},
        {"id": "open_charge_map-9", "source": "open_charge_map", "latitude": -6.20015, "longitude": 106.8000,
         "name": "OCM", "address": None, "province": None, "city": None, "operator": None,
         "charge_type": None, "status": None, "date_verified": None,
         "connectors": [{"type": "CCS2", "count": 2, "speed_tier": "fast",
                         "power_kw": 150.0, "type_inferred": True}]},
    ])
    out = seed_db.build_stations()
    assert len(out) == 1
    s = out[0]
    assert s["id"] == "pln_spklu-1"
    assert sorted(s["sources"]) == ["open_charge_map", "pln_spklu"]
    assert s["power_kw"] == 150.0
    assert s["speed_tier"] == "fast"
    assert sorted(s["connector_types"]) == ["AC Type 2", "CCS2"]
    assert {c["type"] for c in s["connectors"]} == {"AC Type 2", "CCS2"}
```

- [ ] **Step 2: Run, expect failure** `.venv/bin/python -m pytest tests/test_seed_build.py -q` -> FAIL (old `build_stations` overrides fields / no `connectors`).

- [ ] **Step 3: Implement.** In `scripts/seed_db.py`:

(a) Add `import json` to the imports.

(b) Replace `build_stations` with (dedup already sets `connectors` + derived fields):
```python
def build_stations() -> list[dict]:
    return dedup.cluster_stations(sources.normalized_rows())
```

(c) Replace the `_INSERT` statement to include the `connectors` column:
```python
_INSERT = text("""
    INSERT INTO stations
      (id, geom, name, address, province, city, operator, power_kw, speed_tier,
       connector_types, connector_inferred, connectors, sources, status, date_verified)
    VALUES
      (:id, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), :name, :address, :province,
       :city, :operator, :power_kw, :speed_tier, :connector_types, :connector_inferred,
       CAST(:connectors AS jsonb), :sources, :status, :date_verified)
""")
```

(d) In `main()`'s `conn.execute(_INSERT, {...})` param dict, add:
```python
                "connectors": json.dumps(s.get("connectors") or []),
```

- [ ] **Step 4: Run, expect pass** `.venv/bin/python -m pytest tests/test_seed_build.py -q` -> pass. Also `.venv/bin/python -m py_compile scripts/seed_db.py`.

- [ ] **Step 5: Commit**
```bash
git add scripts/seed_db.py tests/test_seed_build.py
git commit -m "feat: seed stations.connectors (jsonb)"
```

---

## Task 7: API surface (repo SELECT + row mapping + spec)

**Files:** Modify `api/stations_repo.py`; Modify `api/main.py`; regen `openapi.json`/`openapi.yaml`.

- [ ] **Step 1: Add `connectors` to the repo SELECT.** In `api/stations_repo.py`, the `_COLS` string lists the selected columns; add `connectors` to it (e.g. after `connector_inferred,`):
```python
    city, operator, power_kw, speed_tier, connector_types, connector_inferred,
    connectors, sources, status, date_verified
```

- [ ] **Step 2: Pass `connectors` in `_row_to_station`.** In `api/main.py`, change the `connectors=None,` argument in `_row_to_station` to:
```python
        connectors=row.get("connectors") or [],
```
(psycopg returns the `jsonb` column as a Python list of dicts; Pydantic coerces it into `list[Connector]`.)

- [ ] **Step 3: Verify import + boot** `.venv/bin/python -c "import api.main; print('ok')"` and `.venv/bin/python -m pytest -q` -> green / DB tests skipped.

- [ ] **Step 4: Regenerate the spec** `.venv/bin/python -m api.export_openapi` and confirm the `Connector` schema is present:
```bash
.venv/bin/python -c "import json; s=json.load(open('openapi.json')); print('Connector' in s['components']['schemas'])"
```
Expect `True`.

- [ ] **Step 5: Commit**
```bash
git add api/stations_repo.py api/main.py openapi.json openapi.yaml
git commit -m "feat: expose station connectors in API + regen spec"
```

---

## Task 8: DB-gated integration test + end-to-end verification

**Files:** Modify `tests/test_stations_db.py` (verification uses a real Postgres; Xendit/wallet untouched).

- [ ] **Step 1: Add a DB-gated test** to `tests/test_stations_db.py` (new function, same `requires_db` + `TestClient(main.app)` pattern as the existing test):
```python
@requires_db
def test_stations_expose_connectors():
    from api import main
    with TestClient(main.app) as c:
        body = c.get("/api/v1/stations?limit=200").json()
        # every station has a connectors list; entries have the documented shape
        for s in body["items"]:
            assert isinstance(s["connectors"], list)
            for conn in s["connectors"]:
                assert set(conn) >= {"type", "count", "speed_tier", "power_kw", "type_inferred"}
                assert conn["type"] in ("CCS2", "AC Type 2")
        # at least one OCM-backed station has real per-connector counts (> 1 connector somewhere)
        assert any(sum(x["count"] for x in s["connectors"]) >= 2 for s in body["items"])
```

- [ ] **Step 2: Start Postgres, migrate, seed, run the suite with the DB:**
```bash
podman rm -f evflow-db-test 2>/dev/null
podman run -d --name evflow-db-test -p 55432:5432 -e POSTGRES_USER=evflow -e POSTGRES_PASSWORD=evflow -e POSTGRES_DB=evflow docker.io/postgis/postgis:16-3.4
export DATABASE_URL="postgresql+psycopg://evflow:evflow@localhost:55432/evflow"
# wait until a host connection succeeds (Postgres restarts once during init), then:
.venv/bin/alembic upgrade head           # 0001 + 0002 + 0003
.venv/bin/python -m scripts.seed_db      # rebuild with connectors
.venv/bin/python -m pytest tests/test_stations_db.py tests/test_connectors.py tests/test_sources.py tests/test_dedup.py tests/test_seed_build.py -q
```
Expect: migrations apply, seed prints a station count, all listed tests pass.

- [ ] **Step 3: Eyeball one real station's connectors** (confirm OCM produced multi-entry with real counts):
```bash
.venv/bin/python - <<'PY'
import os; os.environ["DATABASE_URL"]="postgresql+psycopg://evflow:evflow@localhost:55432/evflow"
from api import stations_repo as r
_, rows = r.list_stations({"source": "open_charge_map"}, limit=500, offset=0)
multi = [x for x in rows if len(x["connectors"]) >= 2]
print("OCM stations:", len(rows), "with >=2 connector entries:", len(multi))
print(multi[0]["connectors"] if multi else (rows[0]["connectors"] if rows else "no rows"))
PY
```
Expect: a non-empty `connectors` list with real `count`/`power_kw`/`speed_tier` and inferred `type`.

- [ ] **Step 4: Tear down** `podman rm -f evflow-db-test`.

- [ ] **Step 5: Commit**
```bash
git add tests/test_stations_db.py
git commit -m "test: stations expose per-connector list (DB-gated)"
```

---

## Self-review

- **Spec coverage:** `connectors` array (Tasks 1,2,4,5,6,7) · OCM real count/power/speed + inferred type (Tasks 1,2) · PLN/OSM single inferred entry via fallback (Task 2) · non-breaking existing fields/filters/lookups (derived in Tasks 1-3, columns kept in Task 4) · honest `type_inferred` (Task 1,5) · JSONB storage (Task 4,6) · dedup merge max-count union-types (Tasks 1,3) · migration + re-seed (Tasks 4,6,8). All covered.
- **Placeholder scan:** none.
- **Type/name consistency:** `build_connectors(connections, charge_type=None)`, `merge_connectors(lists)`, `derive_station_fields(conns) -> {connector_types, power_kw, speed_tier, connector_inferred}`; row key `connectors` (list) and raw `_connections`; `Connector{type,count,speed_tier,power_kw,type_inferred}`; SQL column `connectors jsonb`; used consistently across tasks.
- **Note (refinement from spec):** dedup uses merge-with-max-count (Task 1/3), which preserves the existing union-of-types behavior the current tests assert; this is the spec's updated dedup rule.
