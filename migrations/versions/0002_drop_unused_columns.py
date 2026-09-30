"""drop unused enabled and optimized_path columns

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-30 18:58:05.079055

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: str | Sequence[str] | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column('library_folders', 'enabled')
    op.drop_column('optimized_books', 'optimized_path')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        'optimized_books',
        sa.Column('optimized_path', sa.String(length=4096), nullable=True),
    )
    op.add_column(
        'library_folders',
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='1'),
    )
