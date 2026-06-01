"""End-to-end tests for GET /api/v1/route.

Builds a tiny synthetic road graph (4 nodes in a line) and points the routing
module at it, so the full path — snapping, Dijkstra, GeoJSON output — is exercised
without downloading a real network. Skipped automatically if the API stack
(fastapi / pandas / networkx) isn't installed.
"""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("pandas")
nx = pytest.importorskip("networkx")

from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    # 4 nodes on a line along longitude; bidirectional edges between neighbours.
    coords = {1: (106.80, -6.20), 2: (106.81, -6.20), 3: (106.82, -6.20), 4: (106.83, -6.20)}
    g = nx.MultiDiGraph()
    for n, (x, y) in coords.items():
        g.add_node(n, x=x, y=y)
    for u, v in [(1, 2), (2, 3), (3, 4)]:
        g.add_edge(u, v, length=1000.0, travel_time=60.0)
        g.add_edge(v, u, length=1000.0, travel_time=60.0)

    graph_path = tmp_path / "tiny.graphml"
    nx.write_graphml(g, graph_path)

    from api import main, routing
    monkeypatch.setattr(routing, "GRAPH_PATH", graph_path)
    routing.reload()

    with TestClient(main.app) as c:
        yield c


@pytest.mark.integration
def test_route_between_points_returns_linestring(client):
    r = client.get("/api/v1/route",
                   params={"lat": -6.20, "lon": 106.801, "dest_lat": -6.20, "dest_lon": 106.829})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["geometry"]["type"] == "LineString"
    assert len(body["geometry"]["coordinates"]) >= 2
    assert body["distance_m"] > 0
    assert body["weight"] == "length"
    # path should traverse all four nodes 1->2->3->4
    assert body["node_count"] == 4


@pytest.mark.integration
def test_route_fastest_weight_accepted(client):
    r = client.get("/api/v1/route",
                   params={"lat": -6.20, "lon": 106.801, "dest_lat": -6.20,
                           "dest_lon": 106.829, "weight": "travel_time"})
    assert r.status_code == 200
    assert r.json()["duration_s"] > 0


@pytest.mark.integration
def test_route_requires_a_destination(client):
    r = client.get("/api/v1/route", params={"lat": -6.20, "lon": 106.80})
    assert r.status_code == 422


@pytest.mark.integration
def test_route_unknown_station_404(client):
    r = client.get("/api/v1/route",
                   params={"lat": -6.20, "lon": 106.80, "station_id": "does-not-exist-1"})
    assert r.status_code == 404


@pytest.fixture
def client_with_stations(tmp_path, monkeypatch):
    """Synthetic graph + two injected stations (A near node 2, B near node 4)."""
    coords = {1: (106.80, -6.20), 2: (106.81, -6.20), 3: (106.82, -6.20), 4: (106.83, -6.20)}
    g = nx.MultiDiGraph()
    for n, (x, y) in coords.items():
        g.add_node(n, x=x, y=y)
    for u, v in [(1, 2), (2, 3), (3, 4)]:
        g.add_edge(u, v, length=1000.0, travel_time=60.0)
        g.add_edge(v, u, length=1000.0, travel_time=60.0)
    graph_path = tmp_path / "tiny.graphml"
    nx.write_graphml(g, graph_path)

    import pandas as pd
    from api import data, main, routing
    monkeypatch.setattr(routing, "GRAPH_PATH", graph_path)
    routing.reload()

    stations = pd.DataFrame(
        [
            {"id": "st-a", "name": "Charger A", "source": "pln_spklu",
             "latitude": -6.2001, "longitude": 106.8109, "operator": "PLN", "power_kw": 22.0},
            {"id": "st-b", "name": "Charger B", "source": "pln_spklu",
             "latitude": -6.2001, "longitude": 106.8309, "operator": "PLN", "power_kw": 50.0},
        ],
        columns=data.COLUMNS,
    )
    monkeypatch.setattr(data, "_DF", stations)

    with TestClient(main.app) as c:
        yield c


@pytest.mark.integration
def test_nearest_station_picks_closest(client_with_stations):
    r = client_with_stations.get("/api/v1/route/nearest-station",
                                 params={"lat": -6.2001, "lon": 106.8005})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["station"]["id"] == "st-a"            # node 2 (1 hop) beats st-b at node 4 (3 hops)
    assert b["within_range"] is True
    assert b["candidates_considered"] == 2
    assert b["route"]["geometry"]["type"] == "LineString"
    assert b["route"]["distance_m"] == pytest.approx(1000.0, abs=1.0)


@pytest.mark.integration
def test_nearest_station_out_of_range_flag(client_with_stations):
    r = client_with_stations.get("/api/v1/route/nearest-station",
                                 params={"lat": -6.2001, "lon": 106.8005, "max_range_km": 0.3})
    assert r.status_code == 200
    b = r.json()
    assert b["station"]["id"] == "st-a"            # still the nearest...
    assert b["within_range"] is False             # ...but 1 km > 0.3 km battery range


@pytest.mark.integration
def test_nearest_station_404_when_no_stations(client):
    # `client` fixture has the graph but no stations injected (empty data layer)
    r = client.get("/api/v1/route/nearest-station", params={"lat": -6.20, "lon": 106.80})
    assert r.status_code == 404


@pytest.mark.integration
def test_nearest_station_with_ev_model_derives_range(client_with_stations, monkeypatch):
    from api import evmodels
    monkeypatch.setattr(
        evmodels, "get",
        lambda mid: {"id": "test-ev", "name": "Test EV", "range_km": 100.0} if mid == "test-ev" else None)

    # st-a is 1.0 km by road. SoC 2% × 100 km × 0.85 = 1.7 km -> within range.
    r = client_with_stations.get("/api/v1/route/nearest-station",
                                 params={"lat": -6.2001, "lon": 106.8005,
                                         "ev_model_id": "test-ev", "current_soc": 2})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["station"]["id"] == "st-a"
    assert b["range_used_km"] == pytest.approx(1.7, abs=0.01)
    assert b["within_range"] is True

    # SoC 1% -> 0.85 km < 1.0 km -> out of range (still returns the station).
    r2 = client_with_stations.get("/api/v1/route/nearest-station",
                                  params={"lat": -6.2001, "lon": 106.8005,
                                          "ev_model_id": "test-ev", "current_soc": 1})
    assert r2.json()["within_range"] is False


@pytest.mark.integration
def test_nearest_station_ev_model_requires_soc(client_with_stations):
    r = client_with_stations.get("/api/v1/route/nearest-station",
                                 params={"lat": -6.2001, "lon": 106.8005, "ev_model_id": "test-ev"})
    assert r.status_code == 422


@pytest.mark.integration
def test_ev_models_catalogue_endpoint(client):
    r = client.get("/api/v1/ev-models")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] > 0
    assert all("id" in m and "name" in m for m in body["items"])

    # fetch one by id round-trips
    first_id = body["items"][0]["id"]
    one = client.get(f"/api/v1/ev-models/{first_id}")
    assert one.status_code == 200
    assert one.json()["id"] == first_id

    assert client.get("/api/v1/ev-models/nope-not-real").status_code == 404
