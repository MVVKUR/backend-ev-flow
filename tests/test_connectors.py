"""Unit tests for connector-type inference + speed-tier classification (AC 1.2.1)."""
import pytest

from api import connectors as conn


@pytest.mark.unit
def test_speed_tier_boundaries():
    assert conn.speed_tier(7) == "slow"        # <=7 slow
    assert conn.speed_tier(7.1) == "medium"
    assert conn.speed_tier(50) == "medium"     # <=50 medium
    assert conn.speed_tier(50.1) == "fast"
    assert conn.speed_tier(150) == "fast"      # <=150 fast
    assert conn.speed_tier(180) == "ultra_fast"
    assert conn.speed_tier(0) == "slow"


@pytest.mark.unit
def test_speed_tier_falls_back_to_charge_type_when_power_unknown():
    assert conn.speed_tier(None, "ultrafast") == "ultra_fast"   # normalised
    assert conn.speed_tier(None, "fast") == "fast"
    assert conn.speed_tier(None, None) is None
    assert conn.speed_tier(None, "garbage") is None


@pytest.mark.unit
def test_infer_connectors_ac_vs_dc():
    assert conn.infer_connectors(7) == ["AC Type 2"]
    assert conn.infer_connectors(22) == ["AC Type 2"]      # at the AC/DC split
    assert conn.infer_connectors(50) == ["CCS2"]           # >22 -> DC
    assert conn.infer_connectors(180) == ["CCS2"]


@pytest.mark.unit
def test_infer_connectors_uses_charge_type():
    assert conn.infer_connectors(None, "ultrafast") == ["CCS2"]
    assert conn.infer_connectors(None, "fast") == ["CCS2"]
    assert conn.infer_connectors(None, "slow") == ["AC Type 2"]
    assert conn.infer_connectors(None, "medium") == ["AC Type 2"]


@pytest.mark.unit
def test_infer_connectors_unknown():
    assert conn.infer_connectors(None, None) == []
    assert conn.infer_connectors(None, "belum ada") == []   # unrecognised label


@pytest.mark.unit
def test_normalize_charge_type():
    assert conn.normalize_charge_type("Ultrafast") == "ultra_fast"
    assert conn.normalize_charge_type("MEDIUM") == "medium"
    assert conn.normalize_charge_type("") is None
    assert conn.normalize_charge_type(None) is None
