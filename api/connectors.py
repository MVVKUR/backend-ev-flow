"""Connector-type inference + speed-tier classification (AC 1.2.1).

Our source data has no reliable connector-standard field (PLN has none, OCM's
ConnectionType is empty for Indonesia, OSM is sparse). Rather than scrape Google
Maps (paid API / ToS issues), we **infer** the connector type from data we already
have (`power_kw` and PLN's `charge_type`) using Indonesia's de-facto standard
that public charging is **Type 2 (AC)** + **CCS2 (DC)**.

This is an educated guess, not ground truth: a multi-connector site is tagged with
its *primary* connector only. Stations carry ``connector_inferred=True`` so the UI
can label it "estimated". The schema is identical to what a real Google Places
enrichment would populate, so the inferred values can later be replaced 1:1.

Pure / stdlib-only so it is unit-testable without pandas.
"""
from __future__ import annotations

from typing import Optional

# AC ↔ DC split (kW). At/below this, public charging in Indonesia is AC Type 2;
# above it, DC fast charging (overwhelmingly CCS2).
AC_DC_SPLIT_KW = 22.0

# Speed-tier power boundaries (kW). ultra_fast has no upper bound. AC 1.2.1 anchors
# "ultra-fast > 150 kW".
SPEED_TIERS = [
    {"id": "slow", "label": "Slow", "min_kw": 0.0, "max_kw": 7.0},
    {"id": "medium", "label": "Medium", "min_kw": 7.0, "max_kw": 50.0},
    {"id": "fast", "label": "Fast", "min_kw": 50.0, "max_kw": 150.0},
    {"id": "ultra_fast", "label": "Ultra-fast", "min_kw": 150.0, "max_kw": None},
]

_DC_HINTS = {"fast", "ultrafast", "ultra_fast", "dc"}
_AC_HINTS = {"slow", "medium", "ac"}


def normalize_charge_type(charge_type: Optional[str]) -> Optional[str]:
    """Lower-case + collapse PLN's `ultrafast` to the canonical `ultra_fast`."""
    if not charge_type:
        return None
    c = str(charge_type).strip().lower()
    return "ultra_fast" if c in ("ultrafast", "ultra fast") else c or None


def infer_connectors(power_kw: Optional[float], charge_type: Optional[str] = None) -> list[str]:
    """Infer the primary connector type(s) for a station.

    Returns ``["CCS2"]`` for DC, ``["AC Type 2"]`` for AC, or ``[]`` when neither
    power nor charge_type is known.
    """
    ct = normalize_charge_type(charge_type)
    has_power = power_kw is not None
    if not has_power and ct is None:
        return []
    is_dc = (has_power and power_kw > AC_DC_SPLIT_KW) or (ct in _DC_HINTS)
    if not is_dc and ct not in _AC_HINTS and not has_power:
        return []  # unknown signal
    return ["CCS2"] if is_dc else ["AC Type 2"]


def speed_tier(power_kw: Optional[float], charge_type: Optional[str] = None) -> Optional[str]:
    """Classify a charging speed tier from power (kW), falling back to PLN's label."""
    if power_kw is not None:
        if power_kw <= 7:
            return "slow"
        if power_kw <= 50:
            return "medium"
        if power_kw <= 150:
            return "fast"
        return "ultra_fast"
    ct = normalize_charge_type(charge_type)
    if ct in {"slow", "medium", "fast", "ultra_fast"}:
        return ct
    return None
