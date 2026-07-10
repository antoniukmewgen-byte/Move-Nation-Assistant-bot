"""add phone to users

Revision ID: 450259ccb420
Revises: fee54dee5054
Create Date: 2026-07-10 17:55:20.312208

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "450259ccb420"
down_revision: str | Sequence[str] | None = "fee54dee5054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("phone", sa.String(length=20), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "phone")
