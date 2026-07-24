"""Add subscription save_folder

Revision ID: a1b2c3d4e5f6
Revises: 03132d5514f9
Create Date: 2026-07-19 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "03132d5514f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "subscriptions",
        sa.Column("save_folder", sa.VARCHAR(length=200), nullable=True),
    )
    op.execute(sa.text("UPDATE subscriptions SET save_folder = name WHERE save_folder IS NULL"))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("subscriptions", "save_folder")
