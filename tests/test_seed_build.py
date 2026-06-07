import pytest

from scripts import seed_db
from api import sources


@pytest.mark.unit
def test_build_stations_dedupes_and_sets_fields(monkeypatch):
    monkeypatch.setattr(sources, "normalized_rows", lambda: [
        {"id": "pln_spklu-1", "source": "pln_spklu", "latitude": -6.2000, "longitude": 106.8000,
         "name": "PLN", "address": None, "province": "DKI Jakarta", "city": None, "operator": "PLN",
         "charge_type": "medium", "status": None, "date_verified": None,
         "connectors": [{"type": "AC Type 2", "count": 1, "speed_tier": "medium",
                         "power_kw": 22.0, "type_inferred": True}]},
        {"id": "open_charge_map-9", "source": "open_charge_map", "latitude": -6.20015, "longitude": 106.8000,
         "name": "OCM", "address": None, "province": None, "city": None, "operator": None,
         "charge_type": None, "status": None, "date_verified": None,
         "connectors": [{"type": "CCS2", "count": 2, "speed_tier": "fast",
                         "power_kw": 150.0, "type_inferred": True}]},
    ])
    out = seed_db.build_stations()
    assert len(out) == 1
    s = out[0]
    assert s["id"] == "pln_spklu-1"
    assert sorted(s["sources"]) == ["open_charge_map", "pln_spklu"]
    assert s["power_kw"] == 150.0
    assert s["speed_tier"] == "fast"
    assert sorted(s["connector_types"]) == ["AC Type 2", "CCS2"]
    assert {c["type"] for c in s["connectors"]} == {"AC Type 2", "CCS2"}
