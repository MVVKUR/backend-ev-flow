import pytest

from tests.conftest import requires_db

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient   # noqa: E402


@requires_db
def test_stations_and_lookups_served_from_db():
    from api import main
    with TestClient(main.app) as c:
        assert c.get("/health").json()["stations_loaded"] > 0
        body = c.get("/api/v1/stations?limit=2").json()
        assert body["total"] > 0
        assert "sources" in body["items"][0]
        assert isinstance(body["items"][0]["sources"], list)
        assert c.get("/api/v1/connectors").status_code == 200
        tiers = {t["id"] for t in c.get("/api/v1/speed-tiers").json()}
        assert tiers == {"slow", "medium", "fast", "ultra_fast"}
        near = c.get("/api/v1/stations/nearby?lat=-6.2088&lon=106.8456&radius_km=5&limit=3").json()
        assert all("distance_km" in s for s in near)
