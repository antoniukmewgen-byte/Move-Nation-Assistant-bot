"""group_members: pending flag, unique tag constraint, lookup indexes

Revision ID: fee54dee5054
Revises: f85c02f49ec0
Create Date: 2026-07-07 15:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fee54dee5054"
down_revision: str | Sequence[str] | None = "f85c02f49ec0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite can't ADD CONSTRAINT via a plain ALTER TABLE — batch mode
    # transparently recreates the table instead, which is required for both
    # the new column and the unique constraint below.
    with op.batch_alter_table("group_members") as batch_op:
        batch_op.add_column(
            sa.Column("pending", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.create_unique_constraint(
            "uq_group_members_group_user_tag", ["group_id", "user_id", "tag"]
        )

    op.create_index("ix_group_members_group_id", "group_members", ["group_id"])
    op.create_index("ix_group_members_user_id", "group_members", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_group_members_user_id", table_name="group_members")
    op.drop_index("ix_group_members_group_id", table_name="group_members")

    with op.batch_alter_table("group_members") as batch_op:
        batch_op.drop_constraint("uq_group_members_group_user_tag", type_="unique")
        batch_op.drop_column("pending")
