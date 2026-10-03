"""drop unused metadata columns and add indexes

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-03 13:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: str | Sequence[str] | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index('ix_books_authors', 'books', ['authors'], unique=False)
    op.create_index('ix_books_created_at', 'books', ['created_at'], unique=False)
    op.drop_column('books', 'language')
    op.drop_column('books', 'series_index')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        'books',
        sa.Column('series_index', sa.Float(), nullable=True),
    )
    op.add_column(
        'books',
        sa.Column('language', sa.String(length=32), nullable=True),
    )
    op.drop_index('ix_books_created_at', table_name='books')
    op.drop_index('ix_books_authors', table_name='books')
