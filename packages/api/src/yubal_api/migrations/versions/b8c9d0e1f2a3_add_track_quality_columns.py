"""Add track quality columns (cover_source + enrichment tracking)

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-07-21 22:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tracks") as batch:
        batch.add_column(sa.Column("cover_source", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("last_enriched_at", sa.DateTime(), nullable=True))
        batch.add_column(
            sa.Column("last_enrich_error", sa.String(length=2000), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("tracks") as batch:
        batch.drop_column("last_enrich_error")
        batch.drop_column("last_enriched_at")
        batch.drop_column("cover_source")
