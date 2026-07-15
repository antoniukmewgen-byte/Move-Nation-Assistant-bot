"""add synced_at to groups

Revision ID: 9b129a22a789
Revises: cea469ddca08
Create Date: 2026-07-15 15:30:27.879393

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9b129a22a789"
down_revision: str | Sequence[str] | None = "cea469ddca08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("groups", sa.Column("synced_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("groups", "synced_at")
