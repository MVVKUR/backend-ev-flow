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
| GET | `/api/v1/stats` | Totals, by-source, by-province, by-charge-type, power summary |
| GET | `/api/v1/sources` | Sources with counts |
| GET | `/api/v1/provinces` | Provinces with counts (filter dropdown) |
| GET | `/api/v1/cities?province=` | Cities with counts |

### Filter params (on `/stations` and `/stations.geojson`)
- `source` = `pln_spklu` | `open_charge_map` | `osm`
- `province` exact (case-insensitive), `city` substring, `q` name search
- `min_power`, `max_power` (kW)
- `bbox` = `minLon,minLat,maxLon,maxLat` (Jakarta: `106.55,-6.65,107.10,-5.95`)
- `limit` / `offset`

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
  main.py            # FastAPI app + endpoints
  export_openapi.py  # dump openapi.json / openapi.yaml
openapi.json / .yaml # exported spec
```
