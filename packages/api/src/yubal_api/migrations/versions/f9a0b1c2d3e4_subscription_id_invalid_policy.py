"""Split subscription ID-invalid cleanup from not-in-playlist policy.

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-07-25 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f9a0b1c2d3e4"
down_revision: str | Sequence[str] | None = "e8f9a0b1c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("subscriptions") as batch:
        batch.add_column(
            sa.Column(
                "id_invalid_marking_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )
        batch.add_column(
            sa.Column(
                "id_invalid_cleanup_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch.add_column(
            sa.Column(
                "id_invalid_cleanup_action",
                sa.String(length=20),
                nullable=False,
                server_default="archive",
            )
        )
        batch.add_column(
            sa.Column(
                "id_invalid_cleanup_delay_hours",
                sa.Integer(),
                nullable=False,
                server_default="72",
            )
        )

    # Seed ID-invalid marking from the previous shared switch; move any
    # mistaken offline→wanted cleanup prefs onto the ID-invalid policy.
    op.execute(
        """
        UPDATE subscriptions
        SET id_invalid_marking_enabled = offline_marking_enabled
        """
    )
    op.execute(
        """
        UPDATE subscriptions
        SET
            id_invalid_cleanup_enabled = offline_cleanup_enabled,
            id_invalid_cleanup_action = 'to_wanted',
            id_invalid_cleanup_delay_hours = offline_cleanup_delay_hours,
            offline_cleanup_action = 'archive'
        WHERE offline_cleanup_action = 'to_wanted'
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("subscriptions") as batch:
        batch.drop_column("id_invalid_cleanup_delay_hours")
        batch.drop_column("id_invalid_cleanup_action")
        batch.drop_column("id_invalid_cleanup_enabled")
        batch.drop_column("id_invalid_marking_enabled")
