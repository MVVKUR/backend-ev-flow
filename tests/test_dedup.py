import pytest

from api.dedup import cluster_stations


def _row(id, source, lat, lon, power=None, name=None, conns=None):
    return {"id": id, "source": source, "latitude": lat, "longitude": lon,
            "power_kw": power, "name": name, "address": None, "province": None,
            "city": None, "operator": None, "charge_type": None, "status": None,
            "date_verified": None, "connector_types": conns or []}


@pytest.mark.unit
def test_two_points_within_75m_merge():
    a = _row("pln_spklu-1", "pln_spklu", -6.2000, 106.8000, power=22, conns=["AC Type 2"])
    b = _row("open_charge_map-9", "open_charge_map", -6.20020, 106.8000, power=150, conns=["CCS2"])
    out = cluster_stations([a, b])
    assert len(out) == 1
    s = out[0]
    assert s["id"] == "pln_spklu-1"
    assert sorted(s["sources"]) == ["open_charge_map", "pln_spklu"]
    assert s["power_kw"] == 150
    assert sorted(s["connector_types"]) == ["AC Type 2", "CCS2"]


@pytest.mark.unit
def test_points_over_75m_stay_separate():
    a = _row("pln_spklu-1", "pln_spklu", -6.2000, 106.8000)
    b = _row("pln_spklu-2", "pln_spklu", -6.2050, 106.8050)
    out = cluster_stations([a, b])
    assert len(out) == 2


@pytest.mark.unit
def test_descriptive_fields_fill_from_first_nonnull_by_priority():
    a = _row("pln_spklu-1", "pln_spklu", -6.2000, 106.8000, name="PLN Gambir")
    b = _row("open_charge_map-9", "open_charge_map", -6.20010, 106.8000, name="OCM name")
    a["address"] = None
    b["address"] = "Jl. Test 1"
    out = cluster_stations([a, b])
    assert out[0]["name"] == "PLN Gambir"
    assert out[0]["address"] == "Jl. Test 1"


@pytest.mark.unit
def test_deterministic_pln_anchors_regardless_of_input_order():
    a = _row("pln_spklu-1", "pln_spklu", -6.2000, 106.8000)
    b = _row("osm-node-5", "osm", -6.20010, 106.8000)
    assert cluster_stations([b, a])[0]["id"] == "pln_spklu-1"
    assert cluster_stations([a, b])[0]["id"] == "pln_spklu-1"
