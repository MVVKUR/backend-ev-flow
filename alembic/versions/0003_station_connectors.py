"""station connectors jsonb

Revision ID: 0003_station_connectors
Revises: 0002_wallet_topups
Create Date: 2026-06-07
"""
from alembic import op

revision = "0003_station_connectors"
down_revision = "0002_wallet_topups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE stations ADD COLUMN connectors jsonb NOT NULL DEFAULT '[]';")


def downgrade() -> None:
    op.execute("ALTER TABLE stations DROP COLUMN IF EXISTS connectors;")
