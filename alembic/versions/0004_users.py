"""users

Revision ID: 0004_users
Revises: 0003_station_connectors
Create Date: 2026-06-08
"""
from alembic import op

revision = "0004_users"
down_revision = "0003_station_connectors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE users (
            id                  uuid PRIMARY KEY,
            username            text UNIQUE,
            password_hash       text,
            google_sub          text UNIQUE,
            email               text,
            full_name           text,
            account_type        text NOT NULL DEFAULT 'ev_user',
            ev_model_id         text,
            main_connector_type text,
            location_consent    boolean NOT NULL DEFAULT false,
            location_consent_at timestamptz,
            profile_completed   boolean NOT NULL DEFAULT false,
            created_at          timestamptz NOT NULL DEFAULT now()
        );
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS users;")
