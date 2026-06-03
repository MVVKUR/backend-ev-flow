"""wallet + topups

Revision ID: 0002_wallet_topups
Revises: 0001_stations
Create Date: 2026-06-03
"""
from alembic import op

revision = "0002_wallet_topups"
down_revision = "0001_stations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE wallet (
            id          smallint PRIMARY KEY,
            balance_idr bigint NOT NULL DEFAULT 0 CHECK (balance_idr >= 0),
            updated_at  timestamptz NOT NULL DEFAULT now()
        );
    """)
    op.execute("INSERT INTO wallet (id, balance_idr) VALUES (1, 0);")
    op.execute("""
        CREATE TABLE topups (
            id                uuid PRIMARY KEY,
            external_id       text NOT NULL UNIQUE,
            xendit_invoice_id text UNIQUE,
            amount_idr        bigint NOT NULL CHECK (amount_idr > 0),
            status            text NOT NULL DEFAULT 'pending',
            invoice_url       text,
            created_at        timestamptz NOT NULL DEFAULT now(),
            paid_at           timestamptz
        );
    """)
    op.execute("CREATE INDEX topups_invoice_ix ON topups (xendit_invoice_id);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS topups;")
    op.execute("DROP TABLE IF EXISTS wallet;")
