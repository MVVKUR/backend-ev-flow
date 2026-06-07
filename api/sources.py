"""Source loaders + normalization for PLN / OCM / OSM into one row schema."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

from . import connectors

# dataset/data/raw  (this file lives in dataset/api/)
ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = Path(os.getenv("RAW_DIR", ROOT / "data" / "raw"))

PLN_PATH = RAW_DIR / "_petaspklu_all.json"
OCM_PATH = RAW_DIR / "ocm_jakarta.json"
OSM_PATH = RAW_DIR / "osm_charging_jakarta.json"

COLUMNS = [
    "id", "name", "source", "latitude", "longitude", "address", "province",
    "city", "operator", "power_kw", "charge_type", "connectors", "status",
    "date_verified",
]


def _num(v) -> float:
    try:
        return float(str(v).split()[0])
    except (TypeError, ValueError, IndexError):
        return math.nan


def _load_pln() -> list[dict]:
    if not PLN_PATH.exists():
        return []
    raw = json.loads(PLN_PATH.read_text(encoding="utf-8"))
    out = []
    for r in raw:
        try:
            lat, lon = float(r.get("latitude")), float(r.get("longitude"))
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(lat) and math.isfinite(lon)) or (lat == 0 and lon == 0):
            continue
        out.append({
            "id": f"pln_spklu-{r.get('id')}",
            "name": r.get("nama_lokasi"),
            "source": "pln_spklu",
            "latitude": lat, "longitude": lon,
            "address": r.get("alamat"),
            "province": (r.get("provinsi") or "").strip() or None,
            "city": r.get("kabupaten_kota"),
            "operator": "PLN",
            "power_kw": _num(r.get("watt")),
            "charge_type": r.get("type_charge"),
            "connectors": r.get("total_konektor") or None,
            "status": "operational" if r.get("status") == 1 else (str(r.get("status")) if r.get("status") is not None else None),
            "date_verified": None,
        })
    return out


def _load_ocm() -> list[dict]:
    if not OCM_PATH.exists():
        return []
    raw = json.loads(OCM_PATH.read_text(encoding="utf-8"))
    out = []
    for i, p in enumerate(raw):
        ai = p.get("AddressInfo") or {}
        lat, lon = ai.get("Latitude"), ai.get("Longitude")
        if lat is None or lon is None:
            continue
        conns = p.get("Connections") or []
        power = [c.get("PowerKW") for c in conns if c.get("PowerKW")]
        stat = (p.get("StatusType") or {}).get("IsOperational")
        out.append({
            "id": f"open_charge_map-{p.get('ID', i)}",
            "name": ai.get("Title"),
            "source": "open_charge_map",
            "latitude": float(lat), "longitude": float(lon),
            "address": ai.get("AddressLine1"),
            "province": ai.get("StateOrProvince"),
            "city": ai.get("Town"),
            "operator": (p.get("OperatorInfo") or {}).get("Title"),
            "power_kw": max(power) if power else math.nan,
            "_connections": [{"power_kw": c.get("PowerKW"), "count": c.get("Quantity") or 1}
                             for c in conns],
            "charge_type": None,
            "connectors": p.get("NumberOfPoints") or None,
            "status": None if stat is None else ("operational" if stat else "non-operational"),
            "date_verified": p.get("DateLastVerified"),
        })
    return out


def _load_osm() -> list[dict]:
    if not OSM_PATH.exists():
        return []
    payload = json.loads(OSM_PATH.read_text(encoding="utf-8"))
    out = []
    for el in payload.get("elements", []):
        tags = el.get("tags", {}) or {}
        if el["type"] == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            c = el.get("center") or {}
            lat, lon = c.get("lat"), c.get("lon")
        if lat is None or lon is None:
            continue
        out.append({
            "id": f"osm-{el['type']}-{el['id']}",
            "name": tags.get("name") or tags.get("operator"),
            "source": "osm",
            "latitude": float(lat), "longitude": float(lon),
            "address": tags.get("addr:full") or tags.get("addr:street"),
            "province": None,
            "city": tags.get("addr:city"),
            "operator": tags.get("operator"),
            "power_kw": _num(tags.get("charging_station:output") or tags.get("socket:type2_combo:output")),
            "charge_type": None,
            "connectors": int(_num(tags.get("capacity"))) if not math.isnan(_num(tags.get("capacity"))) else None,
            "status": tags.get("access"),
            "date_verified": None,
        })
    return out


def _clean_power(p):
    if p is None:
        return None
    if isinstance(p, float) and math.isnan(p):
        return None
    return float(p)


def normalized_rows() -> list[dict]:
    """All source rows, normalized, each with a `connectors` list + derived fields."""
    rows = _load_pln() + _load_ocm() + _load_osm()
    out = []
    for r in rows:
        if r.get("latitude") is None or r.get("longitude") is None:
            continue
        conns_in = r.get("_connections")
        if conns_in is None:  # PLN/OSM (and tests): one connection from station power + count
            conns_in = [{"power_kw": _clean_power(r.get("power_kw")), "count": r.get("connectors") or 1}]
        r["connectors"] = connectors.build_connectors(conns_in, r.get("charge_type"))
        derived = connectors.derive_station_fields(r["connectors"])
        r["connector_types"] = derived["connector_types"]
        r["speed_tier"] = derived["speed_tier"]
        r["power_kw"] = derived["power_kw"]
        r["connector_inferred"] = derived["connector_inferred"]
        out.append(r)
    return out
