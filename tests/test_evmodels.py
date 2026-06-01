"""Unit tests for the EV model catalogue parsing + range maths (stdlib only)."""
import pytest

from api import evmodels


@pytest.mark.unit
def test_min_num_parses_single_and_ranges():
    assert evmodels._min_num("26.7 kWh") == 26.7
    assert evmodels._min_num("200 - 300 km") == 200.0   # lower bound (conservative)
    assert evmodels._min_num("51 - 64 kWh") == 51.0
    assert evmodels._min_num("260 km") == 260.0
    assert evmodels._min_num("") is None
    assert evmodels._min_num(None) is None


@pytest.mark.unit
def test_min_num_handles_comma_decimal():
    assert evmodels._min_num("307,5 Juta") == 307.5


@pytest.mark.unit
def test_slug():
    assert evmodels._slug("Wuling Air EV") == "wuling-air-ev"
    assert evmodels._slug("MG 4 EV") == "mg-4-ev"
    assert evmodels._slug("VinFast VF 5") == "vinfast-vf-5"


@pytest.mark.unit
def test_remaining_range_maths():
    # 250 km manufacturer range, 40% SoC, 0.85 safety -> 85 km usable
    assert evmodels.remaining_range_km(250, 40, 0.85) == 85.0
    assert evmodels.remaining_range_km(200, 100, 1.0) == 200.0
    assert evmodels.remaining_range_km(None, 50) is None   # unknown range


@pytest.mark.integration
def test_catalogue_loads_from_committed_zip():
    models = evmodels.reload()
    assert len(models) > 0
    assert all(m["id"] and m["name"] for m in models)      # every model identifiable
    assert any(m["range_km"] for m in models)              # some have a usable range
    assert any(m["battery_kwh"] for m in models)
