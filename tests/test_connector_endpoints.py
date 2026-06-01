"""Integration tests for /connectors, /speed-tiers, and the connector/speed filters."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("pandas")

from fastapi.testclient import TestClient

# ac-1: 22 kW  -> AC Type 2 / medium
# dc-1: 150 kW -> CCS2 / fast
# dc-2: 180 kW -> CCS2 / ultra_fast
_ROWS = [
    {"id": "ac-1", "name": "AC One", "source": "pln_spklu", "latitude": -6.20, "longitude": 106.80,
     "address": None, "province": "DKI Jakarta", "city": "Jakarta", "operator": "PLN",
     "power_kw": 22.0, "charge_type": "medium", "connectors": 2, "status": "operational", "date_verified": None},
    {"id": "dc-1", "name": "DC One", "source": "pln_spklu", "latitude": -6.21, "longitude": 106.81,
     "address": None, "province": "DKI Jakarta", "city": "Jakarta", "operator": "PLN",
     "power_kw": 150.0, "charge_type": "fast", "connectors": 1, "status": "operational", "date_verified": None},
    {"id": "dc-2", "name": "DC Two", "source": "open_charge_map", "latitude": -6.22, "longitude": 106.82,
     "address": None, "province": None, "city": None, "operator": None,
     "power_kw": 180.0, "charge_type": None, "connectors": 1, "status": None, "date_verified": None},
]


@pytest.fixture
def client(monkeypatch):
    from api import data, main
    monkeypatch.setattr(data, "_load_pln", lambda: [r for r in _ROWS if r["source"] == "pln_spklu"])
    monkeypatch.setattr(data, "_load_ocm", lambda: [r for r in _ROWS if r["source"] == "open_charge_map"])
    monkeypatch.setattr(data, "_load_osm", lambda: [])
    data.reload()  # rebuild through the real load() so computed columns are added
    with TestClient(main.app) as c:
        yield c
    data._DF = None  # clear cache for other tests


@pytest.mark.integration
def test_connectors_lookup(client):
    by = {d["name"]: d["count"] for d in client.get("/api/v1/connectors").json()}
    assert by.get("CCS2") == 2          # dc-1, dc-2
    assert by.get("AC Type 2") == 1     # ac-1


@pytest.mark.integration
def test_speed_tiers_lookup(client):
    tiers = {d["id"]: d for d in client.get("/api/v1/speed-tiers").json()}
    assert set(tiers) == {"slow", "medium", "fast", "ultra_fast"}
    assert tiers["medium"]["count"] == 1
    assert tiers["fast"]["count"] == 1
    assert tiers["ultra_fast"]["count"] == 1
    assert tiers["slow"]["count"] == 0
    assert tiers["ultra_fast"]["max_kw"] is None


@pytest.mark.integration
def test_filter_by_connector_type(client):
    body = client.get("/api/v1/stations", params={"connector_type": "CCS2"}).json()
    assert body["total"] == 2
    assert all("CCS2" in s["connector_types"] for s in body["items"])
    assert all(s["connector_inferred"] is True for s in body["items"])


@pytest.mark.integration
def test_filter_by_speed_tier(client):
    assert client.get("/api/v1/stations", params={"speed_tier": "fast"}).json()["total"] == 1


@pytest.mark.integration
def test_station_carries_inferred_fields(client):
    s = client.get("/api/v1/stations/ac-1").json()
    assert s["connector_types"] == ["AC Type 2"]
    assert s["speed_tier"] == "medium"
    assert s["connector_inferred"] is True


@pytest.mark.integration
def test_geojson_supports_connector_filter(client):
    geo = client.get("/api/v1/stations.geojson", params={"connector_type": "CCS2"}).json()
    assert geo["type"] == "FeatureCollection"
    assert len(geo["features"]) == 2
