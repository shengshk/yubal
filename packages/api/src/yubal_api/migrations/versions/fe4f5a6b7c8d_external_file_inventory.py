"""Separate cheap external inventory from expensive metadata indexing.

Revision ID: fe4f5a6b7c8d
Revises: fd3e4f5a6b7c
Create Date: 2026-07-30 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fe4f5a6b7c8d"
down_revision: str | Sequence[str] | None = "fd3e4f5a6b7c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "external_file_inventory",
        sa.Column("rel_path", sa.String(length=1200), primary_key=True),
        sa.Column("dir_name", sa.String(length=255), nullable=False),
        sa.Column("mtime_ns", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("inode", sa.BigInteger()),
        sa.Column(
            "metadata_indexed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "first_seen_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "changed_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )
    op.create_index(
        "ix_external_file_inventory_dir_name",
        "external_file_inventory",
        ["dir_name"],
    )
    op.create_index(
        "ix_external_file_inventory_metadata_indexed",
        "external_file_inventory",
        ["metadata_indexed"],
    )
    op.create_index(
        "ix_external_file_inventory_changed_at",
        "external_file_inventory",
        ["changed_at"],
    )
    # Existing rows are already fully parsed, so they enter the inventory as
    # completed instead of being re-read from SMB after the upgrade.
    op.execute(
        sa.text(
            "INSERT INTO external_file_inventory "
            "(rel_path, dir_name, mtime_ns, size, inode, metadata_indexed, "
            "first_seen_at, changed_at) "
            "SELECT rel_path, dir_name, mtime_ns, size, inode, 1, "
            "updated_at, updated_at FROM external_raw_tracks"
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_external_file_inventory_changed_at",
        table_name="external_file_inventory",
    )
    op.drop_index(
        "ix_external_file_inventory_metadata_indexed",
        table_name="external_file_inventory",
    )
    op.drop_index(
        "ix_external_file_inventory_dir_name",
        table_name="external_file_inventory",
    )
    op.drop_table("external_file_inventory")
