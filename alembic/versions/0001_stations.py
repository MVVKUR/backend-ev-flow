"""stations table + postgis

Revision ID: 0001_stations
Revises:
Create Date: 2026-06-01
"""
from alembic import op

revision = "0001_stations"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
    op.execute("""
        CREATE TABLE stations (
            id                 text PRIMARY KEY,
            geom               geometry(Point, 4326) NOT NULL,
            name               text,
            address            text,
            province           text,
            city               text,
            operator           text,
            power_kw           double precision,
            speed_tier         text,
            connector_types    text[] NOT NULL DEFAULT '{}',
            connector_inferred boolean NOT NULL DEFAULT true,
            sources            text[] NOT NULL DEFAULT '{}',
            status             text,
            date_verified      text
        );
    """)
    op.execute("CREATE INDEX stations_geom_gix ON stations USING GIST (geom);")
    op.execute("CREATE INDEX stations_province_ix ON stations (province);")
    op.execute("CREATE INDEX stations_speed_tier_ix ON stations (speed_tier);")
    op.execute("CREATE INDEX stations_sources_gin ON stations USING GIN (sources);")
    op.execute("CREATE INDEX stations_connector_types_gin ON stations USING GIN (connector_types);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS stations;")
