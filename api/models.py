"""Pydantic request and response models. These drive the OpenAPI (Swagger) schema."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Source(str, Enum):
    pln_spklu = "pln_spklu"
    open_charge_map = "open_charge_map"
    osm = "osm"


class Connector(BaseModel):
    type: str = Field(..., description="Connector standard (inferred from power).", examples=["CCS2"])
    count: int = Field(..., description="Number of this connector at the station.", examples=[2])
    speed_tier: Optional[str] = Field(None, examples=["ultra_fast"])
    power_kw: Optional[float] = Field(None, examples=[200.0])
    type_inferred: bool = Field(True, description="True when the type is inferred from power, not source data.")


class Station(BaseModel):
    id: str = Field(..., description="Stable unique id, '<source>-<n>'.", examples=["pln_spklu-1"])
    name: Optional[str] = Field(None, examples=["SPKLU PLN UID JAKARTA RAYA"])
    sources: list[Source] = Field(
        default_factory=list,
        description="Datasets this station appears in (deduplicated).",
        examples=[["pln_spklu", "open_charge_map"]])
    latitude: float = Field(..., ge=-90, le=90, examples=[-6.18039])
    longitude: float = Field(..., ge=-180, le=180, examples=[106.833191])
    address: Optional[str] = Field(None, examples=["Jl. M.I. Ridwan Rais No.1, Gambir"])
    province: Optional[str] = Field(None, examples=["DKI Jakarta"])
    city: Optional[str] = Field(None, examples=["Kota ADM Jakarta Pusat"])
    operator: Optional[str] = Field(None, examples=["PLN"])
    power_kw: Optional[float] = Field(None, description="Peak power (kW).", examples=[22.0])
    charge_type: Optional[str] = Field(None, description="slow / medium / fast where known.", examples=["medium"])
    speed_tier: Optional[str] = Field(
        None, description="Speed bucket from power: slow / medium / fast / ultra_fast.", examples=["medium"])
    connectors: list[Connector] = Field(
        default_factory=list,
        description="Per-connector breakdown: type (inferred), real count/power/speed.")
    connector_types: list[str] = Field(
        default_factory=list,
        description="Connector standards present, e.g. ['CCS2'] or ['AC Type 2']. Currently inferred.",
        examples=[["AC Type 2"]])
    connector_inferred: Optional[bool] = Field(
        None, description="True when connector_types are inferred from power, not from source data.",
        examples=[True])
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


class SpeedTier(BaseModel):
    """One charging-speed bucket with its power range + station count."""
    id: str = Field(..., examples=["fast"])
    label: str = Field(..., examples=["Fast"])
    min_kw: float = Field(..., description="Lower power bound (kW), inclusive.", examples=[50.0])
    max_kw: Optional[float] = Field(None, description="Upper power bound (kW); null for ultra_fast.", examples=[150.0])
    count: int = Field(..., description="Stations in this tier.", examples=[789])


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
    """RFC 7946 FeatureCollection. Drop straight into Leaflet or Mapbox."""
    type: str = Field("FeatureCollection", examples=["FeatureCollection"])
    features: list[dict[str, Any]]


class Health(BaseModel):
    status: str = Field(..., examples=["ok"])
    stations_loaded: int = Field(..., examples=[3569])
    version: str = Field(..., examples=["1.0.0"])


# ---- routing (Epic 2.0: shortest path via Dijkstra) -------------------------
class RouteGeometry(BaseModel):
    """GeoJSON LineString. Pass it to L.geoJSON() to draw the path on the map."""
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
        None, description="Remaining range (km) used for the within_range check: either the explicit "
                          "max_range_km, or derived from ev_model_id plus current_soc.", examples=[85.0])


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


# ---- wallet / top-up (Epic 3.0: Xendit integration) -------------------------
class TopupRequest(BaseModel):
    amount_idr: int = Field(..., ge=10000, description="Top-up amount in IDR (Xendit min 10000).", examples=[50000])


class TopupCreated(BaseModel):
    topup_id: str
    amount_idr: int
    status: str = Field(..., examples=["pending"])
    invoice_url: str = Field(..., description="Open this hosted Xendit page to pay.")


class WalletBalance(BaseModel):
    balance_idr: int = Field(..., examples=[200000])
    currency: str = Field("IDR", examples=["IDR"])
    updated_at: datetime


class Topup(BaseModel):
    id: str
    external_id: str
    xendit_invoice_id: Optional[str] = None
    amount_idr: int
    status: str = Field(..., examples=["paid"])
    invoice_url: Optional[str] = None
    created_at: datetime
    paid_at: Optional[datetime] = None


# ---- authentication / accounts (Epic 5.0) ------------------------------------
class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, examples=["budi"])
    password: str = Field(..., min_length=8, examples=["s3cret123"])
    full_name: Optional[str] = Field(None, examples=["Budi Santoso"])
    ev_model_id: Optional[str] = Field(None, examples=["hyundai-ioniq-5"])
    main_connector_type: Optional[str] = Field(None, examples=["CCS2"])
    location_consent: bool = False


class LoginRequest(BaseModel):
    username: str
    password: str


class ProfileUpdate(BaseModel):
    username: Optional[str] = Field(None, min_length=3)
    ev_model_id: Optional[str] = None
    main_connector_type: Optional[str] = None
    location_consent: Optional[bool] = None


class UserPublic(BaseModel):
    id: str
    username: Optional[str] = None
    full_name: Optional[str] = None
    email: Optional[str] = None
    account_type: str = "ev_user"
    ev_model_id: Optional[str] = None
    main_connector_type: Optional[str] = None
    location_consent: bool = False
    profile_completed: bool = False
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPublic
