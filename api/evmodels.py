"""EV model catalogue (Kaggle Indonesia-EV-2026): specs for range-aware routing.

Loads make / model / battery / range from the Kaggle dataset (the committed
``ev_dataset.zip``, or an extracted CSV). This is the seed of the Epic 6.0 EVModel
catalogue; for now it backs the optional ``ev_model_id`` + ``current_soc`` inputs on
the nearest-station routing endpoint (Route & Battery), so the backend can derive
the remaining range instead of the client having to know each car's specs.

Stdlib only (csv + zipfile) so it stays light and unit-testable without pandas.
The Kaggle fields are free-text (e.g. "26.7 kWh", "200 - 300 km"); numbers are
parsed out and, where a range is given, the **lower** bound is kept (conservative
for a "can I reach it?" check).
"""
from __future__ import annotations

import csv
import io
import os
import re
import zipfile
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = Path(os.getenv("EV_DATASET_CSV", ROOT / "data" / "raw" / "indonesia_ev_specs_pricing_2026.csv"))
ZIP_PATH = ROOT / "ev_dataset.zip"
ZIP_MEMBER = "indonesia_ev_specs_pricing_2026.csv"

# Plan to arrive with a reserve and discount optimistic manufacturer range.
RANGE_SAFETY_FACTOR = float(os.getenv("ROUTING_RANGE_SAFETY_FACTOR", 0.85))

_MODELS: Optional[list] = None


def _slug(name: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower())).strip("-")


def _numbers(s) -> list:
    if not s:
        return []
    return [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[.,]\d+)?", str(s))]


def _min_num(s) -> Optional[float]:
    nums = _numbers(s)
    return min(nums) if nums else None


def _read_rows() -> list:
    if CSV_PATH.exists():
        with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    if ZIP_PATH.exists():
        with zipfile.ZipFile(ZIP_PATH) as z:
            member = ZIP_MEMBER if ZIP_MEMBER in z.namelist() else next(
                (n for n in z.namelist() if n.endswith(".csv")), None)
            if member is None:
                return []
            with z.open(member) as fh:
                return list(csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig", newline="")))
    return []


def _parse(row: dict) -> Optional[dict]:
    name = (row.get("Vehicle Name") or "").strip()
    if not name:
        return None
    parts = name.split()
    return {
        "id": _slug(name),
        "name": name,
        "make": parts[0],
        "model": " ".join(parts[1:]) or name,
        "battery_kwh": _min_num(row.get("Battery Capacity")),
        "range_km": _min_num(row.get("Range (Jarak Tempuh)")),
        "price_range": (row.get("Vehicle Price Range") or "").strip() or None,
        "charging_time": (row.get("Charging time") or "").strip() or None,
        "source_url": (row.get("Source URL") or "").strip() or None,
    }


def load() -> list:
    """Build (once) and return the EV model catalogue."""
    global _MODELS
    if _MODELS is not None:
        return _MODELS
    seen: dict = {}
    for row in _read_rows():
        m = _parse(row)
        if m and m["id"] not in seen:
            seen[m["id"]] = m
    _MODELS = list(seen.values())
    return _MODELS


def reload() -> list:
    """Force a re-read from disk/zip."""
    global _MODELS
    _MODELS = None
    return load()


def get(model_id: str) -> Optional[dict]:
    return next((m for m in load() if m["id"] == model_id), None)


def search(q: Optional[str], limit: int, offset: int):
    """Return (total, page) for the catalogue, optionally filtered by name."""
    models = load()
    if q:
        ql = q.casefold()
        models = [m for m in models if ql in m["name"].casefold()]
    return len(models), models[offset: offset + limit]


def remaining_range_km(range_km: Optional[float], soc_percent: float,
                       safety_factor: float = RANGE_SAFETY_FACTOR) -> Optional[float]:
    """Usable remaining range (km) = full range × SoC × safety buffer.

    Returns ``None`` when the model's range is unknown (caller should fall back to
    an explicit ``max_range_km``).
    """
    if range_km is None:
        return None
    return round(range_km * (soc_percent / 100.0) * safety_factor, 2)
