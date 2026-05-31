"""FastAPI app exposing combined Jakarta/Indonesia EV charging-station data.

Run:  uvicorn api.main:app --reload --port 8000
Docs: http://localhost:8000/docs   (Swagger UI)
Spec: http://localhost:8000/openapi.json
"""
from __future__ import annotations

import math
from contextlib import asynccontextmanager
from typing import Optional

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import __version__, data
from .models import (
    GeoJSONFeatureCollection, Health, NameCount, Source, SourceCount,
    Station, StationList, Stats,
)

DESCRIPTION = """
REST API over combined **EV charging-station** data for Jakarta / Indonesia,
merging three sources:

* **PLN SPKLU** — official national registry (`petaspklu.id`)
* **Open Charge Map** — crowd-sourced POIs
* **OpenStreetMap** — `amenity=charging_station`

Built for a map frontend: list/filter, nearby search, GeoJSON output, and stats.
"""

TAGS = [
    {"name": "stations", "description": "Query and fetch charging stations."},
    {"name": "geo", "description": "GeoJSON output for direct map rendering."},
    {"name": "meta", "description": "Stats and filter look-ups (sources, provinces, cities)."},
    {"name": "system", "description": "Health/diagnostics."},
]

@asynccontextmanager
async def lifespan(app: FastAPI):
    data.load()  # warm the in-memory dataset at boot
    yield


app = FastAPI(
    title="EV Charging Stations API — Jakarta / Indonesia",
    description=DESCRIPTION,
    version=__version__,
    openapi_tags=TAGS,
    contact={"name": "EV Charging Analysis", "email": "softopen24@gmail.com"},
    license_info={"name": "Data: PLN / OCM (CC-BY-SA) / OSM (ODbL)"},
    lifespan=lifespan,
)

# Frontend will call this from a browser — allow CORS. Tighten origins for production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
        connectors=(int(row["connectors"]) if pd.notna(row["connectors"]) else None),
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
) -> pd.DataFrame:
    out = df
    if source is not None:
        out = out[out["source"] == source.value]
    if province:
        out = out[out["province"].fillna("").str.casefold() == province.casefold()]
    if city:
        out = out[out["city"].fillna("").str.contains(city, case=False, na=False)]
    if q:
        out = out[out["name"].fillna("").str.contains(q, case=False, na=False)]
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
    bbox: Optional[str] = Query(None, description="Bounding box 'minLon,minLat,maxLon,maxLat'.",
                                examples=["106.55,-6.65,107.10,-5.95"]),
    limit: int = Query(100, ge=1, le=1000, description="Page size."),
    offset: int = Query(0, ge=0, description="Page offset."),
) -> StationList:
    df = _apply_filters(data.load(), source, province, city, q, min_power, max_power, bbox)
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
    bbox: Optional[str] = Query(None, examples=["106.55,-6.65,107.10,-5.95"]),
    limit: int = Query(5000, ge=1, le=20000),
) -> GeoJSONFeatureCollection:
    df = _apply_filters(data.load(), source, province, city, q, min_power, max_power, bbox).iloc[:limit]
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
