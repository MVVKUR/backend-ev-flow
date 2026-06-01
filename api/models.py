"""Pydantic response/request models — these drive the OpenAPI (Swagger) schema."""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Source(str, Enum):
    pln_spklu = "pln_spklu"
    open_charge_map = "open_charge_map"
    osm = "osm"


class Station(BaseModel):
    id: str = Field(..., description="Stable unique id, '<source>-<n>'.", examples=["pln_spklu-1"])
    name: Optional[str] = Field(None, examples=["SPKLU PLN UID JAKARTA RAYA"])
    source: Source = Field(..., description="Originating dataset.")
    latitude: float = Field(..., ge=-90, le=90, examples=[-6.18039])
    longitude: float = Field(..., ge=-180, le=180, examples=[106.833191])
    address: Optional[str] = Field(None, examples=["Jl. M.I. Ridwan Rais No.1, Gambir"])
    province: Optional[str] = Field(None, examples=["DKI Jakarta"])
    city: Optional[str] = Field(None, examples=["Kota ADM Jakarta Pusat"])
    operator: Optional[str] = Field(None, examples=["PLN"])
    power_kw: Optional[float] = Field(None, description="Peak power (kW).", examples=[22.0])
    charge_type: Optional[str] = Field(None, description="slow / medium / fast where known.", examples=["medium"])
    connectors: Optional[int] = Field(None, description="Number of connectors/points.", examples=[2])
    status: Optional[str] = Field(None, description="Operational status if reported.", examples=["operational"])
    date_verified: Optional[str] = Field(None, description="ISO timestamp last verified (OCM).")
    distance_km: Optional[float] = Field(None, description="Set only on /nearby results.", examples=[1.42])


class StationList(BaseModel):
    total: int = Field(..., description="Total matching records (before pagination).", examples=[1142])
    limit: int = Field(..., examples=[100])
    offset: int = Field(..., examples=[0])
    items: list[Station]


class SourceCount(BaseModel):
    source: Source
    count: int


class NameCount(BaseModel):
    name: str = Field(..., examples=["DKI Jakarta"])
    count: int = Field(..., examples=[731])


class Stats(BaseModel):
    total: int = Field(..., examples=[3569])
    by_source: list[SourceCount]
    by_province: list[NameCount]
    by_charge_type: list[NameCount]
    with_power_kw: int = Field(..., description="Records that have a known power rating.")
    power_kw_min: Optional[float] = None
    power_kw_max: Optional[float] = None
    power_kw_mean: Optional[float] = None


class GeoJSONFeatureCollection(BaseModel):
    """RFC 7946 FeatureCollection — drop straight into Leaflet/Mapbox."""
    type: str = Field("FeatureCollection", examples=["FeatureCollection"])
    features: list[dict[str, Any]]


class Health(BaseModel):
    status: str = Field(..., examples=["ok"])
    stations_loaded: int = Field(..., examples=[3569])
    version: str = Field(..., examples=["1.0.0"])


# ---- routing (Epic 2.0: shortest path via Dijkstra) -------------------------
class RouteGeometry(BaseModel):
    """GeoJSON LineString — drop straight into `L.geoJSON()` to draw the path."""
    type: str = Field("LineString", examples=["LineString"])
    coordinates: list[list[float]] = Field(
        ..., description="Ordered [longitude, latitude] pairs (WGS84)."
    )


class RoutePoint(BaseModel):
    lat: float = Field(..., examples=[-6.2088])
    lon: float = Field(..., examples=[106.8456])
    snapped_node: str = Field(..., description="Nearest road-graph node the point was snapped to.")
    snap_distance_km: float = Field(..., description="Distance from the input point to the snapped node.")
    station_id: Optional[str] = Field(None, description="Set on the destination when routing to a station.")


class Route(BaseModel):
    """Shortest driving path between two points (Dijkstra over the road graph)."""
    weight: str = Field(..., description="Cost minimised: 'length' (metres) or 'travel_time' (seconds).",
                        examples=["length"])
    distance_m: float = Field(..., description="Total path length in metres.", examples=[4230.5])
    duration_s: float = Field(..., description="Estimated drive time in seconds.", examples=[540.2])
    origin: RoutePoint
    destination: RoutePoint
    node_count: int = Field(..., description="Number of road nodes in the path.", examples=[87])
    geometry: RouteGeometry


class NearestStationRoute(BaseModel):
    """Nearest charging station reachable by road + the route to it (Epic 2.0)."""
    station: Station = Field(..., description="The closest reachable station; its distance_km mirrors the road distance.")
    route: Route
    candidates_considered: int = Field(..., description="How many stations were reachable by road and ranked.",
                                       examples=[1142])
    within_range: bool = Field(True, description="False if the nearest station is beyond the EV's remaining range.")
    range_used_km: Optional[float] = Field(
        None, description="Remaining range (km) used for the within_range check — either the explicit "
                          "max_range_km, or derived from ev_model_id + current_soc.", examples=[85.0])


# ---- EV model catalogue (Kaggle Indonesia-EV-2026; seed of Epic 6.0) --------
class EVModel(BaseModel):
    id: str = Field(..., examples=["wuling-air-ev"])
    name: str = Field(..., examples=["Wuling Air EV"])
    make: Optional[str] = Field(None, examples=["Wuling"])
    model: Optional[str] = Field(None, examples=["Air EV"])
    battery_kwh: Optional[float] = Field(None, description="Usable battery capacity (kWh).", examples=[26.7])
    range_km: Optional[float] = Field(
        None, description="Manufacturer range (km); the lower bound where a range is given.", examples=[200.0])
    price_range: Optional[str] = Field(None, examples=["Rp 214 - 307,5 Juta"])
    charging_time: Optional[str] = Field(None, examples=["8.5 Jam"])
    source_url: Optional[str] = Field(None)


class EVModelList(BaseModel):
    total: int = Field(..., examples=[60])
    limit: int = Field(..., examples=[100])
    offset: int = Field(..., examples=[0])
    items: list[EVModel]
