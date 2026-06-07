import math

import pytest

from api import sources


@pytest.mark.unit
def test_normalized_rows_infers_connector_and_speed(monkeypatch):
    monkeypatch.setattr(sources, "_load_pln", lambda: [{
        "id": "pln_spklu-1", "source": "pln_spklu", "latitude": -6.2, "longitude": 106.8,
        "name": "X", "address": None, "province": "DKI Jakarta", "city": None,
        "operator": "PLN", "power_kw": 150.0, "charge_type": "fast",
        "connectors": 1, "status": "operational", "date_verified": None,
    }])
    monkeypatch.setattr(sources, "_load_ocm", lambda: [])
    monkeypatch.setattr(sources, "_load_osm", lambda: [])
    rows = sources.normalized_rows()
    assert len(rows) == 1
    assert rows[0]["connector_types"] == ["CCS2"]
    assert rows[0]["speed_tier"] == "fast"


@pytest.mark.unit
def test_normalized_rows_nan_power_becomes_none(monkeypatch):
    monkeypatch.setattr(sources, "_load_pln", lambda: [{
        "id": "pln_spklu-2", "source": "pln_spklu", "latitude": -6.2, "longitude": 106.8,
        "name": None, "address": None, "province": None, "city": None, "operator": None,
        "power_kw": math.nan, "charge_type": None, "connectors": None,
        "status": None, "date_verified": None,
    }])
    monkeypatch.setattr(sources, "_load_ocm", lambda: [])
    monkeypatch.setattr(sources, "_load_osm", lambda: [])
    assert sources.normalized_rows()[0]["power_kw"] is None


@pytest.mark.unit
def test_normalized_rows_ocm_builds_per_connector_list(monkeypatch):
    monkeypatch.setattr(sources, "_load_pln", lambda: [])
    monkeypatch.setattr(sources, "_load_ocm", lambda: [{
        "id": "open_charge_map-1", "source": "open_charge_map", "latitude": -6.2, "longitude": 106.8,
        "name": "OCM", "address": None, "province": None, "city": None, "operator": None,
        "power_kw": 200.0, "charge_type": None, "connectors": 3, "status": None, "date_verified": None,
        "_connections": [{"power_kw": 200.0, "count": 2}, {"power_kw": 7.0, "count": 1}],
    }])
    monkeypatch.setattr(sources, "_load_osm", lambda: [])
    row = sources.normalized_rows()[0]
    assert {c["type"]: c["count"] for c in row["connectors"]} == {"CCS2": 2, "AC Type 2": 1}
    assert row["connector_types"] == ["AC Type 2", "CCS2"]
    assert row["power_kw"] == 200.0
    assert row["speed_tier"] == "ultra_fast"
