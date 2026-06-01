"""Data layer: vectorised distance helpers used by routing.

Source loaders and row normalisation have moved to api/sources.py.
The in-memory pandas DataFrame (load/reload/_DF) has been replaced by the
database; see api/db.py and scripts/seed_db.py.
"""
from __future__ import annotations

import numpy as np


def haversine_km(lat1, lon1, lat2, lon2):
    """Vectorised great-circle distance in km."""
    R = 6371.0088
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))
