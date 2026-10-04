"""multi user and per user progression

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-04 12:00:00.000000

"""
from __future__ import annotations

import os
import secrets
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from argon2 import PasswordHasher

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_admin", sa.Boolean(), server_default=sa.false(), nullable=False),
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
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    admin_user = os.environ.get("OPDS_ADMIN_USERNAME") or "admin"
    admin_pass = os.environ.get("OPDS_ADMIN_PASSWORD") or secrets.token_urlsafe(18)
    admin_hash = PasswordHasher().hash(admin_pass)

    users_table = sa.table(
        "users",
        sa.column("id", sa.Integer),
        sa.column("username", sa.String),
        sa.column("password_hash", sa.String),
        sa.column("is_admin", sa.Boolean),
    )
    op.bulk_insert(
        users_table,
        [{"username": admin_user, "password_hash": admin_hash, "is_admin": True}],
    )

    conn = op.get_bind()
    admin_id = conn.execute(
        sa.text("SELECT id FROM users WHERE username = :u"), {"u": admin_user}
    ).scalar()

    naming_convention = {"uq": "uq_%(table_name)s_%(column_0_name)s"}

    with op.batch_alter_table("progressions", naming_convention=naming_convention) as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))

    if admin_id is not None:
        op.execute(
            sa.text(f"UPDATE progressions SET user_id = {admin_id} WHERE user_id IS NULL")
        )

    with op.batch_alter_table("progressions", naming_convention=naming_convention) as batch_op:
        batch_op.alter_column("user_id", nullable=False, existing_type=sa.Integer())
        batch_op.create_foreign_key(
            "fk_progressions_user_id", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.drop_constraint("uq_progressions_book_id", type_="unique")
        batch_op.create_unique_constraint(
            "uq_progressions_user_book", ["user_id", "book_id"]
        )
        batch_op.create_index("ix_progressions_user_id", ["user_id"])
        batch_op.create_index("ix_progressions_book_id", ["book_id"])


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    # Ensure at most 1 progression per book before restoring unique constraint on book_id
    conn.execute(
        sa.text(
            """
            DELETE FROM progressions
            WHERE id NOT IN (
                SELECT p1.id FROM progressions p1
                JOIN (
                    SELECT book_id, MAX(COALESCE(modified, created_at)) as max_mod
                    FROM progressions GROUP BY book_id
                ) p2 ON p1.book_id = p2.book_id
                AND COALESCE(p1.modified, p1.created_at) = p2.max_mod
            )
            """
        )
    )

    naming_convention = {"uq": "uq_%(table_name)s_%(column_0_name)s"}
    with op.batch_alter_table("progressions", naming_convention=naming_convention) as batch_op:
        batch_op.drop_index("ix_progressions_book_id")
        batch_op.drop_index("ix_progressions_user_id")
        batch_op.drop_constraint("uq_progressions_user_book", type_="unique")
        batch_op.drop_constraint("fk_progressions_user_id", type_="foreignkey")
        batch_op.create_unique_constraint("uq_progressions_book_id", ["book_id"])
        batch_op.drop_column("user_id")

    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
