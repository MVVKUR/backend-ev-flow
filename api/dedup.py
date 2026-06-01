"""Deduplicate charging stations: merge points within a radius into one station.

Pure functions, no I/O. Rows are processed in source-priority order (PLN, then
Open Charge Map, then OSM) so a PLN row anchors each cluster, making the result
deterministic. Each input row is a normalized dict with at least: id, source,
latitude, longitude, power_kw, connector_types, and the descriptive fields.
"""
from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
from typing import Optional

MERGE_RADIUS_M = 75.0
SOURCE_PRIORITY = {"pln_spklu": 0, "open_charge_map": 1, "osm": 2}
_DESC_FIELDS = ("name", "address", "province", "city", "operator",
                "charge_type", "status", "date_verified")


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371008.8  # mean earth radius, metres
    p1, p2 = radians(lat1), radians(lat2)
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(p1) * cos(p2) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


def _priority(row: dict) -> int:
    return SOURCE_PRIORITY.get(row.get("source"), 99)


def cluster_stations(rows: list[dict], radius_m: float = MERGE_RADIUS_M) -> list[dict]:
    """Merge rows whose coordinates are within radius_m of a cluster's anchor."""
    ordered = sorted(rows, key=lambda r: (_priority(r), str(r.get("id"))))
    clusters: list[dict] = []
    for r in ordered:
        anchor = None
        for c in clusters:
            if _haversine_m(r["latitude"], r["longitude"], c["latitude"], c["longitude"]) <= radius_m:
                anchor = c
                break
        if anchor is None:
            clusters.append(_new_cluster(r))
        else:
            _merge_into(anchor, r)
    for c in clusters:
        _finalize(c)
    return clusters


def _new_cluster(r: dict) -> dict:
    c = dict(r)
    c["sources"] = [r["source"]]
    c["_powers"] = [r.get("power_kw")]
    c["_conns"] = list(r.get("connector_types") or [])
    return c


def _merge_into(c: dict, r: dict) -> None:
    if r["source"] not in c["sources"]:
        c["sources"].append(r["source"])
    for f in _DESC_FIELDS:
        if not c.get(f) and r.get(f):
            c[f] = r[f]
    c["_powers"].append(r.get("power_kw"))
    for t in (r.get("connector_types") or []):
        if t not in c["_conns"]:
            c["_conns"].append(t)


def _finalize(c: dict) -> None:
    powers = [p for p in c.pop("_powers") if p is not None]
    c["power_kw"] = max(powers) if powers else None
    c["connector_types"] = c.pop("_conns")
    c["connector_inferred"] = True
