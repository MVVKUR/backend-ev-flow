"""Load + dedupe stations into Postgres. Run once after `alembic upgrade head`:

    python -m scripts.seed_db

Reads data/raw/*.json (host-mounted), normalizes, infers connectors, clusters
within 75 m, then truncates and inserts the unique stations.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text          # noqa: E402

from api import connectors, dedup, sources  # noqa: E402
from api.db import engine            # noqa: E402

_INSERT = text("""
    INSERT INTO stations
      (id, geom, name, address, province, city, operator, power_kw, speed_tier,
       connector_types, connector_inferred, sources, status, date_verified)
    VALUES
      (:id, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), :name, :address, :province,
       :city, :operator, :power_kw, :speed_tier, :connector_types, :connector_inferred,
       :sources, :status, :date_verified)
""")


def build_stations() -> list[dict]:
    merged = dedup.cluster_stations(sources.normalized_rows())
    for m in merged:
        m["speed_tier"] = connectors.speed_tier(m.get("power_kw"), m.get("charge_type"))
        m["connector_inferred"] = True
    return merged


def main() -> None:
    stations = build_stations()
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE stations;"))
        for s in stations:
            conn.execute(_INSERT, {
                "id": s["id"], "lat": s["latitude"], "lon": s["longitude"],
                "name": s.get("name"), "address": s.get("address"),
                "province": s.get("province"), "city": s.get("city"),
                "operator": s.get("operator"), "power_kw": s.get("power_kw"),
                "speed_tier": s.get("speed_tier"),
                "connector_types": list(s.get("connector_types") or []),
                "connector_inferred": bool(s.get("connector_inferred", True)),
                "sources": list(s.get("sources") or []),
                "status": s.get("status"), "date_verified": s.get("date_verified"),
            })
    print(f"seeded {len(stations)} stations")


if __name__ == "__main__":
    main()
