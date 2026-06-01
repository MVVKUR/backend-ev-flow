"""FastAPI app exposing combined Jakarta/Indonesia EV charging-station data.

Run:  uvicorn api.main:app --reload --port 8000
Docs: http://localhost:8000/docs   (Swagger UI)
Spec: http://localhost:8000/openapi.json
"""
from __future__ import annotations

import math
import os
from contextlib import asynccontextmanager
from typing import Optional

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import __version__, data, evmodels
from . import connectors as conn
from .models import (
    EVModel, EVModelList, GeoJSONFeatureCollection, Health, NameCount,
    NearestStationRoute, Route, Source, SourceCount, SpeedTier, Station,
    StationList, Stats,
)

DESCRIPTION = """
Charging-station data for Jakarta and the rest of Indonesia. It combines the official PLN
SPKLU registry (petaspklu.id) with Open Charge Map and OpenStreetMap.

Use it to list and filter stations, find what's nearby, get GeoJSON for the map, plan a
route to a charger, and read summary stats.
"""

TAGS = [
    {"name": "stations", "description": "Query and fetch charging stations."},
    {"name": "geo", "description": "GeoJSON output for direct map rendering."},
    {"name": "meta", "description": "Stats and filter look-ups (sources, provinces, cities)."},
    {"name": "ev-models", "description": "EV model catalogue (battery / range) for range-aware routing."},
    {"name": "system", "description": "Health/diagnostics."},
]

@asynccontextmanager
async def lifespan(app: FastAPI):
    data.load()  # warm the in-memory dataset at boot
    yield


app = FastAPI(
    title="Jakarta EV Charging Stations API",
    description=DESCRIPTION,
    version=__version__,
    openapi_tags=TAGS,
    contact={"name": "EV-FLOW", "email": "softopen24@gmail.com"},
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
def _clean(v):
    """JSON-safe scalar: NaN/NaT -> None."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if v is pd.NaT:
        return None
    return v


def _row_to_station(row: pd.Series, distance_km: Optional[float] = None) -> Station:
    return Station(
        id=row["id"],
        name=_clean(row["name"]),
        source=row["source"],
        latitude=float(row["latitude"]),
        longitude=float(row["longitude"]),
        address=_clean(row["address"]),
        province=_clean(row["province"]),
        city=_clean(row["city"]),
        operator=_clean(row["operator"]),
        power_kw=_clean(row["power_kw"]),
        charge_type=_clean(row["charge_type"]),
        speed_tier=(row["speed_tier"] if "speed_tier" in row and _clean(row["speed_tier"]) else None),
        connectors=(int(row["connectors"]) if pd.notna(row["connectors"]) else None),
        connector_types=(list(row["connector_types"]) if "connector_types" in row and row["connector_types"] is not None else []),
        connector_inferred=(bool(row["connector_inferred"]) if "connector_inferred" in row else None),
        status=_clean(row["status"]),
        date_verified=_clean(row["date_verified"]),
        distance_km=(round(distance_km, 3) if distance_km is not None else None),
    )


def _apply_filters(
    df: pd.DataFrame,
    source: Optional[Source],
    province: Optional[str],
    city: Optional[str],
    q: Optional[str],
    min_power: Optional[float],
    max_power: Optional[float],
    bbox: Optional[str],
    connector_type: Optional[str] = None,
    speed_tier: Optional[str] = None,
) -> pd.DataFrame:
    out = df
    if source is not None:
        out = out[out["source"] == source.value]
    if connector_type:
        out = out[out["connector_types"].apply(lambda lst: connector_type in (lst or []))]
    if speed_tier:
        out = out[out["speed_tier"] == speed_tier]
    if province:
        out = out[out["province"].fillna("").str.casefold() == province.casefold()]
    if city:
        out = out[out["city"].fillna("").str.contains(city, case=False, na=False, regex=False)]
    if q:
        out = out[out["name"].fillna("").str.contains(q, case=False, na=False, regex=False)]
    if min_power is not None:
        out = out[out["power_kw"] >= min_power]
    if max_power is not None:
        out = out[out["power_kw"] <= max_power]
    if bbox:
        try:
            min_lon, min_lat, max_lon, max_lat = (float(x) for x in bbox.split(","))
        except ValueError:
            raise HTTPException(422, "bbox must be 'minLon,minLat,maxLon,maxLat'")
        out = out[
            out["latitude"].between(min_lat, max_lat)
            & out["longitude"].between(min_lon, max_lon)
        ]
    return out


# ----------------------------------------------------------------------------- endpoints
@app.get("/health", response_model=Health, tags=["system"], summary="Liveness + dataset size")
def health() -> Health:
    return Health(status="ok", stations_loaded=len(data.load()), version=__version__)


@app.get("/api/v1/stations", response_model=StationList, tags=["stations"],
         summary="List / filter charging stations")
def list_stations(
    source: Optional[Source] = Query(None, description="Filter by dataset."),
    province: Optional[str] = Query(None, description="Exact province match (case-insensitive), e.g. 'DKI Jakarta'."),
    city: Optional[str] = Query(None, description="City/kabupaten substring match."),
    q: Optional[str] = Query(None, description="Case-insensitive search on station name."),
    min_power: Optional[float] = Query(None, ge=0, description="Min power (kW)."),
    max_power: Optional[float] = Query(None, ge=0, description="Max power (kW)."),
    connector_type: Optional[str] = Query(None, description="Connector standard, e.g. 'CCS2' or 'AC Type 2' (see /api/v1/connectors). Currently inferred.", examples=["CCS2"]),
    speed_tier: Optional[str] = Query(None, description="Speed tier: slow / medium / fast / ultra_fast (see /api/v1/speed-tiers)."),
    bbox: Optional[str] = Query(None, description="Bounding box 'minLon,minLat,maxLon,maxLat'.",
                                examples=["106.55,-6.65,107.10,-5.95"]),
    limit: int = Query(100, ge=1, le=1000, description="Page size."),
    offset: int = Query(0, ge=0, description="Page offset."),
) -> StationList:
    df = _apply_filters(data.load(), source, province, city, q, min_power, max_power, bbox,
                        connector_type=connector_type, speed_tier=speed_tier)
    total = len(df)
    page = df.iloc[offset: offset + limit]
    return StationList(
        total=total, limit=limit, offset=offset,
        items=[_row_to_station(r) for _, r in page.iterrows()],
    )


@app.get("/api/v1/stations/nearby", response_model=list[Station], tags=["stations"],
         summary="Nearest stations to a point ('near me')")
def nearby(
    lat: float = Query(..., ge=-90, le=90, description="Origin latitude.", examples=[-6.2088]),
    lon: float = Query(..., ge=-180, le=180, description="Origin longitude.", examples=[106.8456]),
    radius_km: float = Query(5.0, gt=0, le=500, description="Search radius (km)."),
    limit: int = Query(20, ge=1, le=200, description="Max results, sorted by distance."),
    source: Optional[Source] = Query(None, description="Optional source filter."),
) -> list[Station]:
    df = data.load()
    if source is not None:
        df = df[df["source"] == source.value]
    if df.empty:
        return []
    d = data.haversine_km(lat, lon, df["latitude"].values, df["longitude"].values)
    df = df.assign(_d=d)
    near = df[df["_d"] <= radius_km].nsmallest(limit, "_d")
    return [_row_to_station(r, distance_km=r["_d"]) for _, r in near.iterrows()]


@app.get("/api/v1/stations/{station_id}", response_model=Station, tags=["stations"],
         summary="Fetch one station by id", responses={404: {"description": "Not found"}})
def get_station(station_id: str) -> Station:
    df = data.load()
    hit = df[df["id"] == station_id]
    if hit.empty:
        raise HTTPException(404, f"station '{station_id}' not found")
    return _row_to_station(hit.iloc[0])


@app.get("/api/v1/stations.geojson", response_model=GeoJSONFeatureCollection, tags=["geo"],
         summary="Stations as a GeoJSON FeatureCollection")
def stations_geojson(
    source: Optional[Source] = Query(None),
    province: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    min_power: Optional[float] = Query(None, ge=0),
    max_power: Optional[float] = Query(None, ge=0),
    connector_type: Optional[str] = Query(None, examples=["CCS2"]),
    speed_tier: Optional[str] = Query(None),
    bbox: Optional[str] = Query(None, examples=["106.55,-6.65,107.10,-5.95"]),
    limit: int = Query(5000, ge=1, le=20000),
) -> GeoJSONFeatureCollection:
    df = _apply_filters(data.load(), source, province, city, q, min_power, max_power, bbox,
                        connector_type=connector_type, speed_tier=speed_tier).iloc[:limit]
    features = []
    for _, r in df.iterrows():
        st = _row_to_station(r)
        props = st.model_dump(exclude={"latitude", "longitude", "distance_km"})
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(r["longitude"]), float(r["latitude"])]},
            "properties": props,
        })
    return GeoJSONFeatureCollection(type="FeatureCollection", features=features)


@app.get("/api/v1/route", response_model=Route, tags=["geo"],
         summary="Shortest driving path (Dijkstra) to a point or station",
         responses={404: {"description": "Station not found / no drivable route"},
                    422: {"description": "Destination not provided"},
                    503: {"description": "Road graph unavailable (not built yet)"}})
def route(
    lat: float = Query(..., ge=-90, le=90, description="Origin latitude.", examples=[-6.2088]),
    lon: float = Query(..., ge=-180, le=180, description="Origin longitude.", examples=[106.8456]),
    station_id: Optional[str] = Query(None, description="Destination = this station's coordinates."),
    dest_lat: Optional[float] = Query(None, ge=-90, le=90, description="Destination latitude (if no station_id)."),
    dest_lon: Optional[float] = Query(None, ge=-180, le=180, description="Destination longitude (if no station_id)."),
    weight: str = Query("length", pattern="^(length|travel_time)$",
                        description="Minimise 'length' (shortest) or 'travel_time' (fastest)."),
) -> Route:
    if station_id:
        hit = data.load()[data.load()["id"] == station_id]
        if hit.empty:
            raise HTTPException(404, f"station '{station_id}' not found")
        dest_lat, dest_lon = float(hit.iloc[0]["latitude"]), float(hit.iloc[0]["longitude"])
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
    lat: float = Query(..., ge=-90, le=90, description="Origin latitude.", examples=[-6.2088]),
    lon: float = Query(..., ge=-180, le=180, description="Origin longitude.", examples=[106.8456]),
    source: Optional[Source] = Query(None, description="Optional source filter."),
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
    df = data.load()
    if source is not None:
        df = df[df["source"] == source.value]
    if df.empty:
        raise HTTPException(404, "no charging stations loaded")

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

    from . import routing  # deferred: pulls in networkx/the road graph only when routing is used
    try:
        result = routing.nearest_station_route(
            lat, lon, df["id"].tolist(), df["latitude"].to_numpy(), df["longitude"].to_numpy(),
            weight=weight, max_range_km=range_used,
        )
    except routing.GraphUnavailable as e:
        raise HTTPException(503, f"routing unavailable: {e}")
    if result is None:
        raise HTTPException(404, "no charging station reachable by road from this point")

    hit = df[df["id"] == result["station_id"]].iloc[0]
    return NearestStationRoute(
        station=_row_to_station(hit, distance_km=result["route"]["distance_m"] / 1000.0),
        route=result["route"],
        candidates_considered=result["candidates_considered"],
        within_range=result["within_range"],
        range_used_km=range_used,
    )


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
    df = data.load()
    power = df["power_kw"].dropna()
    by_source = [SourceCount(source=s, count=int(c)) for s, c in df["source"].value_counts().items()]
    by_prov = [NameCount(name=str(n), count=int(c))
               for n, c in df["province"].fillna("(unknown)").value_counts().head(40).items()]
    by_type = [NameCount(name=str(n), count=int(c))
               for n, c in df["charge_type"].fillna("(unknown)").value_counts().items()]
    return Stats(
        total=len(df),
        by_source=by_source,
        by_province=by_prov,
        by_charge_type=by_type,
        with_power_kw=int(power.size),
        power_kw_min=float(power.min()) if power.size else None,
        power_kw_max=float(power.max()) if power.size else None,
        power_kw_mean=round(float(power.mean()), 2) if power.size else None,
    )


@app.get("/api/v1/sources", response_model=list[SourceCount], tags=["meta"],
         summary="Sources with counts")
def sources() -> list[SourceCount]:
    df = data.load()
    return [SourceCount(source=s, count=int(c)) for s, c in df["source"].value_counts().items()]


@app.get("/api/v1/provinces", response_model=list[NameCount], tags=["meta"],
         summary="Provinces with counts (filter dropdown)")
def provinces() -> list[NameCount]:
    df = data.load()
    vc = df["province"].dropna().value_counts()
    return [NameCount(name=str(n), count=int(c)) for n, c in vc.items()]


@app.get("/api/v1/cities", response_model=list[NameCount], tags=["meta"],
         summary="Cities with counts (optionally within a province)")
def cities(province: Optional[str] = Query(None, description="Restrict to one province.")) -> list[NameCount]:
    df = data.load()
    if province:
        df = df[df["province"].fillna("").str.casefold() == province.casefold()]
    vc = df["city"].dropna().value_counts()
    return [NameCount(name=str(n), count=int(c)) for n, c in vc.items()]


@app.get("/api/v1/connectors", response_model=list[NameCount], tags=["meta"],
         summary="Connector types with counts for the filter dropdown (inferred)")
def connectors_lookup() -> list[NameCount]:
    df = data.load()
    counts: dict[str, int] = {}
    for types in df["connector_types"]:
        for t in (types or []):
            counts[t] = counts.get(t, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return [NameCount(name=n, count=c) for n, c in ordered]


@app.get("/api/v1/speed-tiers", response_model=list[SpeedTier], tags=["meta"],
         summary="Speed tier definitions with counts (AC 1.2.1)")
def speed_tiers_lookup() -> list[SpeedTier]:
    df = data.load()
    counts = {str(k): int(v) for k, v in df["speed_tier"].value_counts().items()}
    return [
        SpeedTier(id=t["id"], label=t["label"], min_kw=t["min_kw"], max_kw=t["max_kw"],
                  count=counts.get(t["id"], 0))
        for t in conn.SPEED_TIERS
    ]
