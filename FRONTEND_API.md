# EV-FLOW — Frontend / Map Integration Guide

Everything the **frontend (map) team** needs to build against this backend. This is the
contract: base URL, conventions, every map-facing endpoint with real request/response
examples, and an honest field-coverage table so the UI is designed around data that
actually exists.

> Interactive reference (always the source of truth): **Swagger UI → `/docs`**, ReDoc → `/redoc`,
> raw spec → `/openapi.json`. This doc is the human-friendly summary.

---

## 1. Base URL & environments

| Env | Base URL |
|-----|----------|
| Local dev | `http://localhost:8000` |
| Production | `https://<your-domain>` once deployed via Podman + Cloudflare Tunnel (see [DEPLOY.md](DEPLOY.md)) |

All resource paths are versioned under **`/api/v1`** (except `/health`). Pin to `v1`.
Interactive contract is live at `<base>/docs` (Swagger) and `<base>/openapi.json`.

```js
const API = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";
```

## 2. Auth

Iteration 1 **map endpoints are public — no auth/token needed.** (Auth applies only to the
later account/payment endpoints, Epic 6.0.) Just `fetch()` directly from the browser.

## 3. Conventions (read this — it prevents 90% of map bugs)

- **CRS**: WGS84 / EPSG:4326 — standard lat-lon, exactly what Leaflet/Mapbox expect.
- **Coordinate order**:
  - **GeoJSON output** uses `[longitude, latitude]` (per RFC 7946).
  - **Query params** (`lat`, `lon`, `dest_lat`, …) are passed separately, so no ambiguity.
- **bbox format**: `minLon,minLat,maxLon,maxLat` (note: lon first). Jakarta example:
  `106.55,-6.65,107.10,-5.95`. Use it on map pan/zoom to fetch only the viewport.
- **Pagination**: list endpoints return `{ total, limit, offset, items }`. `total` is the
  full match count before paging.
- **Privacy (UU PDP / AC 1.1.2)**: round the user's GPS on the **client** before sending it
  (≈3 decimals ≈ 100 m). The server never stores raw coordinates.
- **Errors**: FastAPI default shape — `{"detail": "message"}` with the proper HTTP status
  (404 not found, 422 invalid params, 503 routing unavailable).

## 4. Map endpoints

### 4.1 `GET /api/v1/stations.geojson` — primary map layer ⭐
Drop straight into Leaflet/Mapbox. Call on map load and on pan/zoom with the viewport `bbox`.

Query params: `bbox`, `province`, `city`, `q`, `min_power`, `max_power`, `connector_type`, `speed_tier`,
`limit` (default 5000, max 20000).

```js
const geo = await (await fetch(
  `${API}/api/v1/stations.geojson?bbox=106.55,-6.65,107.10,-5.95`
)).json();
L.geoJSON(geo).addTo(map);   // markers rendered
```

Response (RFC 7946 FeatureCollection):
```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": { "type": "Point", "coordinates": [106.833191, -6.18039] },
      "properties": {
        "id": "pln_spklu-1",
        "name": "SPKLU PLN UID JAKARTA RAYA",
        "sources": ["pln_spklu"],
        "address": "Jl. M.I. Ridwan Rais No.1, Gambir",
        "province": "DKI Jakarta",
        "city": "Kota ADM Jakarta Pusat",
        "operator": "PLN",
        "power_kw": 22.0,
        "speed_tier": "medium",
        "connector_types": ["AC Type 2"],
        "connector_inferred": true,
        "status": "operational",
        "date_verified": null
      }
    }
  ]
}
```

### 4.2 `GET /api/v1/stations/nearby` — "near me" / map opens at user position
Satisfies AC 1.1.1 (default 5 km radius). Results are sorted by distance and each carries
`distance_km`.

Query params: `lat`, `lon` (both optional, pass them together), `radius_km` (default 5, max 500),
`limit` (default 20, max 200), plus the same filters as `/stations`: `connector_type`, `speed_tier`,
`min_power`, `max_power`.
- **With `lat`+`lon`** (location granted): stations within the radius, sorted by distance, each with `distance_km`.
- **Without them** (location denied): a filtered list (no `distance_km`, not distance-sorted).
- Passing only one of `lat`/`lon` returns `422`.

So one endpoint covers both cases: "filter + near me" when you have location, "filter only" when you do not.

```js
const near = await (await fetch(
  `${API}/api/v1/stations/nearby?lat=-6.2088&lon=106.8456&radius_km=5&limit=20`
)).json();
```
```json
[
  { "id": "pln_spklu-1", "name": "SPKLU PLN UID JAKARTA RAYA", "latitude": -6.18039,
    "longitude": 106.833191, "power_kw": 22.0, "operator": "PLN",
    "status": "operational", "distance_km": 1.42 }
]
```

### 4.3 `GET /api/v1/stations/{id}` — marker click → popup detail
```js
const s = await (await fetch(`${API}/api/v1/stations/pln_spklu-1`)).json();
```
Returns one `Station` object (same fields as in the GeoJSON `properties`, plus
`latitude`/`longitude`). `404` if the id doesn't exist.

### 4.4 `GET /api/v1/stations` — list view (beside the map)
Same filters as the GeoJSON endpoint, plus `limit` (default 100, max 1000) and `offset`.
```json
{ "total": 1142, "limit": 100, "offset": 0, "items": [ { "...Station..." } ] }
```

### 4.5 Filter dropdowns (lookups)

| Endpoint | Returns | Use |
|---|---|---|
| `GET /api/v1/provinces` | `[{name, count}]` | Province dropdown |
| `GET /api/v1/cities?province=` | `[{name, count}]` | City dropdown (cascades from province) |
| `GET /api/v1/connectors` | `[{name, count}]` | **Connector-type chips** (CCS2 / AC Type 2 …) — AC 1.2.1 |
| `GET /api/v1/speed-tiers` | `[{id, label, min_kw, max_kw, count}]` | **Speed-tier filter** (slow/medium/fast/ultra_fast) |
| `GET /api/v1/stats` | totals + breakdowns | Landing-page summary chips |

```json
// GET /api/v1/connectors  (counts are over inferred connector types — see §5)
[ { "name": "CCS2", "count": 1320 }, { "name": "AC Type 2", "count": 980 } ]

// GET /api/v1/speed-tiers
[ { "id": "slow", "label": "Slow", "min_kw": 0, "max_kw": 7, "count": 210 },
  { "id": "ultra_fast", "label": "Ultra-fast", "min_kw": 150, "max_kw": null, "count": 240 } ]
```

**Filtering by them** — `connector_type` and `speed_tier` work on both `/stations` and
`/stations.geojson`:
```js
// only CCS2 ultra-fast chargers in the viewport
`${API}/api/v1/stations.geojson?connector_type=CCS2&speed_tier=ultra_fast&bbox=...`
```
**Multi-select:** `connector_type` and `speed_tier` are repeatable. Repeat the param to OR them
(works on `/stations`, `/stations.geojson`, and `/nearby`):
```
?speed_tier=fast&speed_tier=ultra_fast&connector_type=CCS2&connector_type=AC%20Type%202
```
Within a filter the values are ORed (any match); across filters they are ANDed. In JS:
`selected.forEach(v => params.append("speed_tier", v))`.

⚠️ `connector_type` is **inferred** (see §5) — stations carry `connector_inferred: true`. It's
the station's *primary* connector, so don't treat a missing type as "definitely no CCS2".

### 4.6 `GET /api/v1/route` — shortest driving path (Dijkstra) ⭐ _(Epic 2.0)_
Computes a road route from an origin to either a **station** or an arbitrary point, and
returns a **GeoJSON `LineString`** you draw directly. The frontend never sees graph internals.

Query params:
- `lat`, `lon` — origin (needed to route; `422` if omitted)
- **destination** — either `station_id`, **or** both `dest_lat` + `dest_lon`
- `weight` — `length` (shortest distance, default) or `travel_time` (fastest)

```js
const r = await (await fetch(
  `${API}/api/v1/route?lat=-6.2088&lon=106.8456&station_id=pln_spklu-1&weight=length`
)).json();

L.geoJSON(r.geometry).addTo(map);          // draw the route polyline
console.log(`${(r.distance_m/1000).toFixed(1)} km, ~${Math.round(r.duration_s/60)} min`);
```
```json
{
  "weight": "length",
  "distance_m": 4230.5,
  "duration_s": 540.2,
  "origin":      { "lat": -6.2088,  "lon": 106.8456,   "snapped_node": "1234", "snap_distance_km": 0.03 },
  "destination": { "lat": -6.18039, "lon": 106.833191, "snapped_node": "5678", "snap_distance_km": 0.01,
                   "station_id": "pln_spklu-1" },
  "node_count": 87,
  "geometry": {
    "type": "LineString",
    "coordinates": [[106.8456,-6.2088], [106.8461,-6.2079], "...", [106.833191,-6.18039]]
  }
}
```
Errors: `404` (station not found / no drivable route), `422` (no destination given),
`503` (road graph not built yet — backend must run the graph build first).

### Mapbox GL variant
```js
map.addSource("route", { type: "geojson", data: r.geometry });
map.addLayer({ id: "route", type: "line", source: "route",
               paint: { "line-width": 4, "line-color": "#1d6feb" } });
```

### 4.7 `GET /api/v1/route/nearest-station` — closest charger + route ⭐ _(Route & Battery)_
"Route me to the nearest charger." One Dijkstra run finds the closest station **by road**
(not crow-flies) and returns it plus the route. Pass `max_range_km` (the EV's remaining
range) to get a **battery-reachability flag** — the answer to *"can I make it?"*.

Two ways to express the battery range:
- **Pass a vehicle (recommended):** `ev_model_id` + `current_soc` (%) — the backend derives
  the remaining range from the EV catalogue (see §4.8). Just send what the user picked.
- **Pass a number:** `max_range_km` — if you've already computed remaining range yourself.

`ev_model_id` overrides `max_range_km`. Other params: `lat`, `lon` (origin; needed to route, `422` if omitted),
`weight` (`length`|`travel_time`).

```js
// the user selected their car + saw their charge level — send those, not maths
const res = await (await fetch(
  `${API}/api/v1/route/nearest-station?lat=-6.2088&lon=106.8456&ev_model_id=wuling-air-ev&current_soc=40`
)).json();

if (!res.within_range)
  showWarning(`Nearest charger is ${(res.route.distance_m/1000).toFixed(1)} km — beyond your ~${res.range_used_km} km range`);
L.geoJSON(res.route.geometry).addTo(map);
```
```json
{
  "station": { "id": "pln_spklu-42", "name": "SPKLU ...", "operator": "PLN",
               "latitude": -6.19, "longitude": 106.84, "power_kw": 50.0,
               "distance_km": 4.23, "...": "..." },
  "route": { "distance_m": 4230.5, "duration_s": 540.2, "geometry": { "type": "LineString", "coordinates": ["..."] }, "...": "..." },
  "candidates_considered": 1142,
  "within_range": true,
  "range_used_km": 68.0
}
```
Notes: `within_range` is `false` (with a 200, not an error) when the nearest charger is
farther than the range — the UI can still show it and warn. `range_used_km` is the range the
check used (derived or explicit). `422` if `ev_model_id` is sent without `current_soc`;
`404` if station/model not found or none reachable by road; `503` if the graph isn't built.
Requires the station dataset (`data/raw/`).

> Range derivation: `range_used_km = model.range_km × (current_soc/100) × 0.85` (a safety
> buffer; manufacturer range is optimistic). When the full Epic 6.0 EVModel catalogue lands,
> this gets more accurate — **the request/response shape won't change.**

### 4.8 `GET /api/v1/ev-models` — EV catalogue (for the vehicle picker)
Backs the `ev_model_id` input above. Sourced from the Kaggle Indonesia-EV-2026 dataset (~60 models).

```js
const { items } = await (await fetch(`${API}/api/v1/ev-models?q=wuling`)).json();
// -> [{ id:"wuling-air-ev", name:"Wuling Air EV", make:"Wuling", model:"Air EV",
//       battery_kwh:26.7, range_km:200, price_range:"Rp 214 - 307,5 Juta", charging_time:"8.5 Jam" }]
```
- `GET /api/v1/ev-models?q=&limit=&offset=` → `{ total, limit, offset, items }`
- `GET /api/v1/ev-models/{id}` → one model (`404` if not found)

⚠️ `range_km` is a manufacturer figure (lower bound when a range like "200-300 km" is given);
`connector_type` and `max_intake_power` are **not** in this dataset (see §5).

## 5. Field coverage — design the UI around real data ⚠️

Not every field is populated. Coverage by source (from our data analysis):

| Field | Reliability | UI guidance |
|---|---|---|
| `sources` (list of contributing datasets, deduplicated) | ✅ All stations | Informational only (which datasets list this station, e.g. a "verified by" badge). Not a filter. |
| `latitude` / `longitude` | ✅ All sources | Always present |
| `power_kw` | ✅ Good | Safe |
| `speed_tier` (slow/medium/fast/ultra_fast) | ✅ Derived from `power_kw` | **Best filter to expose** |
| `charge_type` | always null from the database (superseded by `speed_tier`) | Use `speed_tier` instead; `charge_type` is no longer populated |
| `operator` | ✅ PLN, ⚠️ OCM sparse | Fallback `"Unknown"` |
| `address` | ✅ PLN, ❌ OCM/OSM sparse | OCM → show `"{city}, {province}"`; OSM → coords only |
| `province` / `city` | ✅ PLN & OCM | OSM often null |
| `connectors` (count) | always null from the database (superseded by `connector_types`) | Use `connector_types` list instead; this count field is no longer populated |
| `connector_types` (CCS2 / AC Type 2) | ⚠️ **Inferred** from power (`connector_inferred: true`) | Filterable, but label as "estimated"; don't use it to *hide* stations |
| `status` | ⚠️ Inconsistent across sources | Don't hard-filter on it |
| `date_verified` | OCM only | Optional badge |

Per-source row counts (Jabodetabek): **PLN 1142, OCM 527, OSM 13**. Note: counts are per included source after deduplication, so a station merged from two sources counts toward both (the per-source counts can sum to more than the total station count).

## 6. Roadmap (so you can plan UI, not block on it)

| Capability | Status |
|---|---|
| Station discovery + GeoJSON + nearby + filters | ✅ Available now |
| Connector-type + speed-tier filters & lookups (AC 1.2.1) | ✅ Available (connector inferred; `/connectors`, `/speed-tiers`) |
| `GET /api/v1/route` (Dijkstra shortest path) | ✅ Available (needs graph built once) |
| `GET /api/v1/route/nearest-station` (closest charger + battery range) | ✅ Available (needs graph + station data) |
| `GET /api/v1/ev-models` (catalogue for the vehicle picker) | ✅ Available |
| `GET /api/v1/connectors`, `/speed-tiers` (filter lookups, AC 1.2.1) | ✅ Available |
| Real (non-inferred) connector types + counts via Google Places API | 🔜 Planned (swaps in behind same fields) |
| Tariff & payment endpoints (Epic 6.0) | 🔜 Later iteration |
| Demand heatmap / B2B (Epic 4.0/5.0) | ⏸ Deferred (Iteration 3) |

## 7. CORS

The API currently allows all origins (`*`, GET only) for development — your `fetch` calls
work out of the box. Before production the backend will restrict `allow_origins` to the
frontend's deployed origin; send your dev + prod origins to the backend team.
