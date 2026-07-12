"""add last_message_text to groups

Revision ID: cea469ddca08
Revises: 450259ccb420
Create Date: 2026-07-12 23:53:57.251898

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cea469ddca08"
down_revision: str | Sequence[str] | None = "450259ccb420"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("groups", sa.Column("last_message_text", sa.String(length=4096), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("groups", "last_message_text")
