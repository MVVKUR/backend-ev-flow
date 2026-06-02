"""FastAPI app exposing combined Jakarta/Indonesia EV charging-station data.

Run:  uvicorn api.main:app --reload --port 8000
Docs: http://localhost:8000/docs   (Swagger UI)
Spec: http://localhost:8000/openapi.json
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import __version__, evmodels
from . import connectors as conn
from . import stations_repo as repo
from .models import (
    EVModel, EVModelList, GeoJSONFeatureCollection, Health, NameCount,
    NearestStationRoute, Route, SourceCount, SpeedTier, Station,
    StationList, Stats,
)


TAGS = [
    {"name": "stations", "description": "Query and fetch charging stations."},
    {"name": "geo", "description": "GeoJSON output for direct map rendering."},
    {"name": "meta", "description": "Stats and filter look-ups (sources, provinces, cities)."},
    {"name": "ev-models", "description": "EV model catalogue (battery / range) for range-aware routing."},
    {"name": "system", "description": "Health/diagnostics."},
]

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="Jakarta EV Charging Stations API",
    version=__version__,
    openapi_tags=TAGS,
    license_info={"name": "Data: PLN, OCM (CC-BY-SA), OSM (ODbL)"},
    lifespan=lifespan,
)

# Frontend calls this from a browser, so allow CORS. Restrict origins for production via
# CORS_ALLOW_ORIGINS (comma-separated list); defaults to "*" (open, fine for read-only public
# data, lock it down once auth/write endpoints are added).
_origins_env = os.getenv("CORS_ALLOW_ORIGINS", "*").strip()
_allow_origins = ["*"] if _origins_env in ("", "*") else [o.strip() for o in _origins_env.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ----------------------------------------------------------------------------- helpers
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


# ----------------------------------------------------------------------------- endpoints
@app.get("/health", response_model=Health, tags=["system"], summary="Liveness + dataset size")
def health() -> Health:
    try:
        n = repo.count()
    except Exception:
        n = 0
    return Health(status="ok", stations_loaded=n, version=__version__)


@app.get("/api/v1/stations", response_model=StationList, tags=["stations"],
         summary="List / filter charging stations")
def list_stations(
    province: Optional[str] = Query(None, description="Exact province match (case-insensitive), e.g. 'DKI Jakarta'."),
    city: Optional[str] = Query(None, description="City/kabupaten substring match."),
    q: Optional[str] = Query(None, description="Case-insensitive search on station name."),
    min_power: Optional[float] = Query(None, ge=0, description="Min power (kW)."),
    max_power: Optional[float] = Query(None, ge=0, description="Max power (kW)."),
    connector_type: Optional[list[str]] = Query(None, description="Connector standard(s); repeatable for multi-select (OR), e.g. ?connector_type=CCS2&connector_type=AC%20Type%202 (see /api/v1/connectors)."),
    speed_tier: Optional[list[str]] = Query(None, description="Speed tier(s); repeatable for multi-select (OR): slow / medium / fast / ultra_fast (see /api/v1/speed-tiers)."),
    bbox: Optional[str] = Query(None, description="Bounding box 'minLon,minLat,maxLon,maxLat'.",
                                examples=["106.55,-6.65,107.10,-5.95"]),
    limit: int = Query(100, ge=1, le=1000, description="Page size."),
    offset: int = Query(0, ge=0, description="Page offset."),
) -> StationList:
    filters = {"province": province, "city": city, "q": q,
               "min_power": min_power, "max_power": max_power,
               "connector_type": connector_type, "speed_tier": speed_tier, "bbox": _bbox(bbox)}
    total, rows = repo.list_stations(filters, limit, offset)
    return StationList(total=total, limit=limit, offset=offset,
                       items=[_row_to_station(r) for r in rows])


@app.get("/api/v1/stations/nearby", response_model=list[Station], tags=["stations"],
         summary="Nearest stations to a point ('near me')")
def nearby(lat: Optional[float] = Query(None, ge=-90, le=90, description="Origin latitude. Omit (with lon) if location is denied."),
           lon: Optional[float] = Query(None, ge=-180, le=180, description="Origin longitude. Omit (with lat) if location is denied."),
           radius_km: float = Query(5.0, gt=0, le=500), limit: int = Query(20, ge=1, le=200),
           connector_type: Optional[list[str]] = Query(None),
           speed_tier: Optional[list[str]] = Query(None),
           min_power: Optional[float] = Query(None, ge=0),
           max_power: Optional[float] = Query(None, ge=0)) -> list[Station]:
    filters = {"connector_type": connector_type, "speed_tier": speed_tier,
               "min_power": min_power, "max_power": max_power}
    if lat is not None and lon is not None:
        rows = repo.nearby(lat, lon, radius_km, limit, filters)      # sorted by distance
    elif lat is None and lon is None:
        _, rows = repo.list_stations(filters, limit, 0)              # no location: filter only
    else:
        raise HTTPException(422, "provide both lat and lon, or neither")
    return [_row_to_station(r) for r in rows]


@app.get("/api/v1/stations/{station_id}", response_model=Station, tags=["stations"],
         summary="Fetch one station by id", responses={404: {"description": "Not found"}})
def get_station(station_id: str) -> Station:
    row = repo.get_station(station_id)
    if row is None:
        raise HTTPException(404, f"station '{station_id}' not found")
    return _row_to_station(row)


@app.get("/api/v1/stations.geojson", response_model=GeoJSONFeatureCollection, tags=["geo"],
         summary="Stations as a GeoJSON FeatureCollection")
def stations_geojson(
    province: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    min_power: Optional[float] = Query(None, ge=0),
    max_power: Optional[float] = Query(None, ge=0),
    connector_type: Optional[list[str]] = Query(None),
    speed_tier: Optional[list[str]] = Query(None),
    bbox: Optional[str] = Query(None), limit: int = Query(5000, ge=1, le=20000),
) -> GeoJSONFeatureCollection:
    filters = {"province": province, "city": city, "q": q,
               "min_power": min_power, "max_power": max_power,
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


@app.get("/api/v1/route", response_model=Route, tags=["geo"],
         summary="Shortest driving path (Dijkstra) to a point or station",
         responses={404: {"description": "Station not found / no drivable route"},
                    422: {"description": "Destination not provided"},
                    503: {"description": "Road graph unavailable (not built yet)"}})
def route(
    lat: Optional[float] = Query(None, ge=-90, le=90, description="Origin latitude (needed to route).", examples=[-6.2088]),
    lon: Optional[float] = Query(None, ge=-180, le=180, description="Origin longitude (needed to route).", examples=[106.8456]),
    station_id: Optional[str] = Query(None, description="Destination = this station's coordinates."),
    dest_lat: Optional[float] = Query(None, ge=-90, le=90, description="Destination latitude (if no station_id)."),
    dest_lon: Optional[float] = Query(None, ge=-180, le=180, description="Destination longitude (if no station_id)."),
    weight: str = Query("length", pattern="^(length|travel_time)$",
                        description="Minimise 'length' (shortest) or 'travel_time' (fastest)."),
) -> Route:
    if lat is None or lon is None:
        raise HTTPException(422, "origin 'lat' and 'lon' are required")
    if station_id:
        row = repo.get_station(station_id)
        if row is None:
            raise HTTPException(404, f"station '{station_id}' not found")
        dest_lat, dest_lon = float(row["latitude"]), float(row["longitude"])
    elif dest_lat is None or dest_lon is None:
        raise HTTPException(422, "provide either 'station_id' or both 'dest_lat' and 'dest_lon'")

    from . import routing  # deferred: pulls in networkx/the road graph only when routing is used
    try:
        result = routing.shortest_path(lat, lon, dest_lat, dest_lon, weight=weight)
    except routing.GraphUnavailable as e:
        raise HTTPException(503, f"routing unavailable: {e}")
    if result is None:
        raise HTTPException(404, "no drivable route found between the two points")
    if station_id:
        result["destination"]["station_id"] = station_id
    return result


@app.get("/api/v1/route/nearest-station", response_model=NearestStationRoute, tags=["geo"],
         summary="Nearest charging station reachable by road (Dijkstra) + route to it",
         responses={404: {"description": "No stations loaded / none reachable by road"},
                    503: {"description": "Road graph unavailable (not built yet)"}})
def nearest_station(
    lat: Optional[float] = Query(None, ge=-90, le=90, description="Origin latitude (needed to route).", examples=[-6.2088]),
    lon: Optional[float] = Query(None, ge=-180, le=180, description="Origin longitude (needed to route).", examples=[106.8456]),
    weight: str = Query("length", pattern="^(length|travel_time)$",
                        description="Rank by 'length' (nearest) or 'travel_time' (quickest)."),
    max_range_km: Optional[float] = Query(
        None, gt=0,
        description="EV remaining range (km). Flags whether the nearest charger is within reach (Route & Battery)."),
    ev_model_id: Optional[str] = Query(
        None, description="EV model id (see /api/v1/ev-models). With current_soc the backend derives the "
                          "remaining range, overriding max_range_km."),
    current_soc: Optional[float] = Query(
        None, ge=0, le=100, description="Current state of charge (%); required when ev_model_id is given."),
) -> NearestStationRoute:
    if lat is None or lon is None:
        raise HTTPException(422, "origin 'lat' and 'lon' are required")
    range_used = max_range_km
    if ev_model_id is not None:
        if current_soc is None:
            raise HTTPException(422, "current_soc is required when ev_model_id is given")
        model = evmodels.get(ev_model_id)
        if model is None:
            raise HTTPException(404, f"ev model '{ev_model_id}' not found")
        range_used = evmodels.remaining_range_km(model["range_km"], current_soc)
        if range_used is None:
            raise HTTPException(422, f"range unknown for ev model '{ev_model_id}'; pass max_range_km instead")

    coords = repo.routing_coords()
    if not coords:
        raise HTTPException(404, "no charging stations loaded")

    from . import routing  # deferred: pulls in networkx/the road graph only when routing is used
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
    if row is None:
        raise HTTPException(404, "nearest station resolved by routing but not found")
    return NearestStationRoute(
        station=_row_to_station(row, distance_km=result["route"]["distance_m"] / 1000.0),
        route=result["route"], candidates_considered=result["candidates_considered"],
        within_range=result["within_range"], range_used_km=range_used)


@app.get("/api/v1/ev-models", response_model=EVModelList, tags=["ev-models"],
         summary="List EV models (catalogue from the Kaggle Indonesia-EV-2026 dataset)")
def ev_models(
    q: Optional[str] = Query(None, description="Case-insensitive search on vehicle name."),
    limit: int = Query(100, ge=1, le=500, description="Page size."),
    offset: int = Query(0, ge=0, description="Page offset."),
) -> EVModelList:
    total, items = evmodels.search(q, limit, offset)
    return EVModelList(total=total, limit=limit, offset=offset, items=[EVModel(**m) for m in items])


@app.get("/api/v1/ev-models/{model_id}", response_model=EVModel, tags=["ev-models"],
         summary="Fetch one EV model by id", responses={404: {"description": "Not found"}})
def ev_model(model_id: str) -> EVModel:
    m = evmodels.get(model_id)
    if m is None:
        raise HTTPException(404, f"ev model '{model_id}' not found")
    return EVModel(**m)


@app.get("/api/v1/stats", response_model=Stats, tags=["meta"], summary="Aggregate statistics")
def stats() -> Stats:
    s = repo.stats()
    by_source = [SourceCount(source=src, count=c) for src, c in repo.source_counts()]
    by_prov = [NameCount(name=n, count=c) for n, c in repo.provinces()[:40]]
    by_type = [NameCount(name=k, count=v) for k, v in repo.speed_tier_counts().items()]
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
