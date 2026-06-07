# Per-connector enrichment (OCM level)

**Date:** 2026-06-07
**Status:** Approved design, pending implementation plan

## Context

The frontend wants to show, per station, a list of connectors with type, count, and speed
(e.g. "CCS 2 / Ultra-fast / Total 2"). Today the backend only stores station-level data:
one `power_kw`, one `speed_tier`, and a flat `connector_types text[]` (inferred type names).
There is no slot for per-connector detail, so the richer view is impossible as-is.

We measured the raw OCM data (`data/raw/ocm_jakarta.json`, 527 POIs, 795 connection rows):
- `Quantity` (count) filled on 795/795 (100%)
- `PowerKW` filled on 793/795 (99.7%)
- `ConnectionType` (the connector type) filled on 0/795 (0%)

So OCM gives us **real per-connector count and power** (hence real speed tier), but **no real
connector type**. Our current pipeline discards this per-connection detail by collapsing it into
one inferred type plus the max power.

This feature stops discarding that detail: it adds a per-connector structure, keeps OCM's real
count/power/speed, and infers only the type (CCS2 for DC > 22 kW, AC Type 2 otherwise), flagged
honestly. Google Places (real types, including CHAdeMO) is deferred; the schema is built to hold
it later with no further migration.

## Goals / success criteria

- Stations expose a `connectors` array: `[{type, count, speed_tier, power_kw, type_inferred}]`.
- For OCM-sourced stations: `count`, `power_kw`, and `speed_tier` are real; `type` is inferred.
- For PLN/OSM-only stations: a single inferred connector entry (count 1), as today.
- Existing fields and filters keep working unchanged (non-breaking): `connector_types`,
  `speed_tier`, `power_kw`, `connector_inferred`, the `connector_type`/`speed_tier` filters,
  and the `/connectors` and `/speed-tiers` lookups.
- Honest provenance: every inferred type carries `type_inferred: true`. The frontend may choose
  not to render an "estimated" label; that is a UI decision and does not change the API.

## Decisions locked

- Scope (b): change schema AND fill with real OCM data. Places deferred.
- Storage: a `connectors jsonb` column on `stations` (not a child table); the list is always read
  and written with the station and is never queried independently.
- Keep the existing station-level columns as values DERIVED from `connectors`, so nothing breaks.
- Dedup merges the connector lists across clustered points, grouping by `(type, power_kw)`; the
  count is the MAX across sources, not the sum. This preserves the union of known connector types
  (no regression vs the current union behavior) while avoiding double-counting the same physical
  connectors that two sources both report (clustered points within 75 m are the same station).
- Connector type names stay canonical: `"CCS2"` and `"AC Type 2"` (frontend relabels for display).

## Schema (Alembic migration 0003)

```
ALTER TABLE stations ADD COLUMN connectors jsonb NOT NULL DEFAULT '[]';
```
All existing columns stay. No index on `connectors` (filtering stays on `connector_types text[]`
via the existing GIN index). Re-seed required after the migration because the data changes.

Each element of `connectors`:
```json
{ "type": "CCS2", "count": 2, "speed_tier": "ultra_fast", "power_kw": 200, "type_inferred": true }
```

## Models (`api/models.py`)

```python
class Connector(BaseModel):
    type: str                      # "CCS2" / "AC Type 2" (inferred from power)
    count: int                     # real from OCM Quantity; fallback 1
    speed_tier: str                # real, from this connector's power
    power_kw: Optional[float] = None
    type_inferred: bool            # true at OCM level
```
`Station` gains `connectors: list[Connector]` (default `[]`). The existing
`connectors: Optional[int]` model field (a count, currently unpopulated) is replaced by this list.
Existing Station fields stay.

## Pipeline (`api/sources.py`, `api/connectors.py`)

- `connectors.py`: add a pure helper, e.g. `build_connectors(connections)`, that turns a list of
  `{power_kw, quantity}` into the aggregated connector list: infer `type` and `speed_tier` from
  each power, then merge entries sharing the same `(type, power_kw)` by summing `count`.
- OCM loader (`_load_ocm`): instead of collapsing `Connections`, emit a per-station
  `connectors` list built from each connection's `PowerKW` + `Quantity`.
- PLN / OSM loaders: emit a single connector entry from the station's inferred type and power
  (count 1), so they still carry a `connectors` list of length 0 or 1.

## Dedup (`api/dedup.py`)

Each input row now carries a `connectors` list. During clustering, collect the connector lists of
all clustered points. In `_finalize`, merge them with `connectors.merge_connectors(...)` (group by
`(type, power_kw)`, count = MAX across sources), then recompute the derived station fields with
`connectors.derive_station_fields(...)`. This keeps the union of types while not double-counting.

## Derived fields (computed from `connectors` at seed/normalize time)

- `connector_types` = sorted distinct `type` values across `connectors` (feeds the existing
  `connector_type` multi-select filter and GIN index).
- `power_kw` = max `power_kw` across `connectors` (station headline).
- `speed_tier` = the `speed_tier` of the highest-power connector (station headline; feeds the
  existing `speed_tier` filter).
- `connector_inferred` = true while types are inferred (always at OCM level).

## API surface

- `GET /api/v1/stations`, `/stations.geojson`, `/stations/nearby`, `/stations/{id}`: each Station
  now includes `connectors`. All existing fields remain.
- Filters unchanged: `connector_type` (multi) matches on derived `connector_types`; `speed_tier`
  (multi) matches the station-level `speed_tier`.
- `/api/v1/connectors` and `/api/v1/speed-tiers` lookups unchanged.

## Testing

- Unit (`tests/test_connectors.py`): `build_connectors` aggregates by `(type, power_kw)`, sums
  counts, infers type and speed per power; empty input yields `[]`.
- Unit (`tests/test_sources.py`): OCM normalized rows carry a multi-entry `connectors` list with
  real counts; PLN/OSM rows carry a single inferred entry.
- Unit (`tests/test_dedup.py`): a cluster of a PLN point (AC Type 2 @ 22) and an OCM point
  (CCS2 @ 150) yields a merged `connectors` with both types; counts are MAX (not summed); derived
  `connector_types` is the union and `power_kw` is 150.
- Unit (`tests/test_seed_build.py`): derived `connector_types`, `power_kw`, `speed_tier` match the
  `connectors` list.
- DB-gated (`tests/test_stations_db.py`): a seeded OCM station exposes `connectors` with > 1 entry
  and real `count`; `connector_type`/`speed_tier` filters still behave.

## Migration + re-seed

1. `alembic upgrade head` (applies 0003).
2. `python -m scripts.seed_db` (rebuild stations with the new `connectors`).
On the VPS, run both inside the api container after pulling and rebuilding.

## Out of scope (deferred)

- Google Places enrichment (real connector types, including CHAdeMO, and per-type availability).
  The schema already holds it; a later enrichment fills `type` and flips `type_inferred` to false.
- Any change to how the frontend labels connectors (UI decision).
- Filtering by per-connector speed (the `speed_tier` filter stays station-level).
