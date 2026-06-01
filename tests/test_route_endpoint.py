"""End-to-end tests for GET /api/v1/route (DB-free subset).

Builds a tiny synthetic road graph (4 nodes in a line) and points the routing
module at it, so the full path — snapping, Dijkstra, GeoJSON output — is exercised
without downloading a real network. Skipped automatically if the API stack
(fastapi / networkx) isn't installed.

Only tests that do NOT touch the station repository (no station_id, no
nearest-station endpoint) are kept here. DB-dependent tests live in
tests/test_stations_db.py.
"""
import pytest

pytest.importorskip("fastapi")
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
