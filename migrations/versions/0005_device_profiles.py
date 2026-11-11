"""device profiles configuration

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-11 12:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "device_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("target_width", sa.Integer(), nullable=False),
        sa.Column("target_height", sa.Integer(), nullable=False),
        sa.Column("cbz_dither", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("cbz_split_spreads", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("cbz_rotate_spreads", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("epub_grayscale", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("epub_contrast_boost", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("epub_quality", sa.Integer(), server_default=sa.text("70"), nullable=False),
        sa.Column("epub_remove_fonts", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("epub_remove_unused_css", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("epub_text_cleanup", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_device_profiles_name", "device_profiles", ["name"], unique=True)

    profiles_table = sa.table(
        "device_profiles",
        sa.column("name", sa.String),
        sa.column("display_name", sa.String),
        sa.column("enabled", sa.Boolean),
        sa.column("target_width", sa.Integer),
        sa.column("target_height", sa.Integer),
        sa.column("cbz_dither", sa.Boolean),
        sa.column("cbz_split_spreads", sa.Boolean),
        sa.column("cbz_rotate_spreads", sa.Boolean),
        sa.column("epub_grayscale", sa.Boolean),
        sa.column("epub_contrast_boost", sa.Boolean),
        sa.column("epub_quality", sa.Integer),
        sa.column("epub_remove_fonts", sa.Boolean),
        sa.column("epub_remove_unused_css", sa.Boolean),
        sa.column("epub_text_cleanup", sa.Boolean),
    )

    op.bulk_insert(
        profiles_table,
        [
            {
                "name": "x3",
                "display_name": "Xteink X3",
                "enabled": True,
                "target_width": 528,
                "target_height": 792,
                "cbz_dither": True,
                "cbz_split_spreads": True,
                "cbz_rotate_spreads": True,
                "epub_grayscale": True,
                "epub_contrast_boost": True,
                "epub_quality": 70,
                "epub_remove_fonts": True,
                "epub_remove_unused_css": True,
                "epub_text_cleanup": True,
            },
            {
                "name": "x4",
                "display_name": "Xteink X4",
                "enabled": True,
                "target_width": 480,
                "target_height": 800,
                "cbz_dither": True,
                "cbz_split_spreads": True,
                "cbz_rotate_spreads": True,
                "epub_grayscale": True,
                "epub_contrast_boost": True,
                "epub_quality": 70,
                "epub_remove_fonts": True,
                "epub_remove_unused_css": True,
                "epub_text_cleanup": True,
            },
        ],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_device_profiles_name", table_name="device_profiles")
    op.drop_table("device_profiles")
