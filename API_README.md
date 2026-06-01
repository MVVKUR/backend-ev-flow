# EV Charging Stations API — Jakarta / Indonesia

FastAPI backend serving the combined **PLN SPKLU + Open Charge Map + OpenStreetMap**
charging-station data (3,569 stations) to a frontend.

## Run

```bash
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000
```

- **Swagger UI** → http://localhost:8000/docs
- **ReDoc** → http://localhost:8000/redoc
- **OpenAPI spec** → http://localhost:8000/openapi.json

A static copy of the spec is also exported to [openapi.json](openapi.json) / [openapi.yaml](openapi.yaml):

```bash
python -m api.export_openapi
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness + dataset size |
| GET | `/api/v1/stations` | List/filter (source, province, city, q, min/max power, bbox) + pagination |
| GET | `/api/v1/stations/nearby` | Nearest stations to `lat`/`lon` within `radius_km` ("near me") |
| GET | `/api/v1/stations/{id}` | One station by id |
| GET | `/api/v1/stations.geojson` | Same filters → GeoJSON FeatureCollection (Leaflet/Mapbox) |
| GET | `/api/v1/route` | Shortest driving path (Dijkstra) to a `station_id` or `dest_lat`/`dest_lon` → GeoJSON `LineString` + distance/duration |
| GET | `/api/v1/route/nearest-station` | Nearest charger reachable by road + route; `ev_model_id`+`current_soc` (or `max_range_km`) flags battery reachability |
| GET | `/api/v1/ev-models` | EV model catalogue (battery/range) from Kaggle dataset; `/{id}` for one |
| GET | `/api/v1/stats` | Totals, by-source, by-province, by-charge-type, power summary |
| GET | `/api/v1/sources` | Sources with counts |
| GET | `/api/v1/provinces` | Provinces with counts (filter dropdown) |
| GET | `/api/v1/cities?province=` | Cities with counts |
| GET | `/api/v1/connectors` | Connector types with counts (inferred) — AC 1.2.1 filter |
| GET | `/api/v1/speed-tiers` | Speed-tier definitions with counts — AC 1.2.1 filter |

### Filter params (on `/stations` and `/stations.geojson`)
- `source` = `pln_spklu` | `open_charge_map` | `osm`
- `province` exact (case-insensitive), `city` substring, `q` name search
- `min_power`, `max_power` (kW)
- `connector_type` = `CCS2` | `AC Type 2` (inferred; see `/connectors`)
- `speed_tier` = `slow` | `medium` | `fast` | `ultra_fast` (see `/speed-tiers`)
- `bbox` = `minLon,minLat,maxLon,maxLat` (Jakarta: `106.55,-6.65,107.10,-5.95`)
- `limit` / `offset`

> `connector_type` is **inferred** from power (Indonesia = Type 2 AC / CCS2 DC); stations carry
> `connector_inferred: true`. Real values can later come from the Google Places API behind the
> same field. `speed_tier` is derived from `power_kw`.

## Frontend examples

```js
// List fast chargers in DKI Jakarta
const r = await fetch("http://localhost:8000/api/v1/stations?province=DKI%20Jakarta&min_power=50&limit=100");
const { total, items } = await r.json();

// Render straight onto a Leaflet/Mapbox map
const geo = await (await fetch(
  "http://localhost:8000/api/v1/stations.geojson?bbox=106.55,-6.65,107.10,-5.95"
)).json();
L.geoJSON(geo).addTo(map);

// "Near me"
const near = await (await fetch(
  "http://localhost:8000/api/v1/stations/nearby?lat=-6.2088&lon=106.8456&radius_km=3&limit=20"
)).json();

// Shortest driving route to a station, then draw it
const route = await (await fetch(
  "http://localhost:8000/api/v1/route?lat=-6.2088&lon=106.8456&station_id=pln_spklu-1"
)).json();
L.geoJSON(route.geometry).addTo(map);   // distance_m / duration_s in the body
```

> **Frontend / map team:** see **[FRONTEND_API.md](FRONTEND_API.md)** for the full
> map-integration contract (conventions, every endpoint, field-coverage caveats).

## Routing (`/api/v1/route`)
Dijkstra over the Jakarta drivable road network. The graph is built **once** and cached as
GraphML; the API then loads it with NetworkX (no OSMnx needed at runtime):
```bash
python scripts/build_road_graph.py          # writes data/processed/jakarta_drive.graphml
```
Until the graph is built, `/api/v1/route` returns **503**; all other endpoints work normally.
Pass `weight=travel_time` for fastest-route instead of shortest-distance.

## Tests
```bash
pytest -q          # unit tests for the Dijkstra core + /route endpoint (synthetic graph)
```

## Notes
- **CORS** is open (`*`) for development — restrict `allow_origins` in [api/main.py](api/main.py) for production.
- Data is loaded once into memory from `data/raw/` at startup (~3.5k rows). To refresh after re-pulling source data, restart the server (or call `api.data.reload()`).
- Source files: `_petaspklu_all.json` (PLN), `ocm_jakarta.json` (OCM), `osm_charging_jakarta.json` (OSM).

## Project layout
```
api/
  __init__.py        # version
  models.py          # Pydantic schemas → drive the OpenAPI spec
  data.py            # load + normalise PLN/OCM/OSM into one DataFrame
  connectors.py      # infer connector type + speed tier from power (AC 1.2.1)
  routing.py         # Dijkstra shortest-path over the road graph (/route)
  main.py            # FastAPI app + endpoints
  export_openapi.py  # dump openapi.json / openapi.yaml
scripts/
  build_road_graph.py  # one-off: build & cache the routing GraphML (OSMnx)
tests/                 # pytest suite (Dijkstra core + /route endpoint)
openapi.json / .yaml   # exported spec
FRONTEND_API.md        # map-integration contract for the frontend team
```
