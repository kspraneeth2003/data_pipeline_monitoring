"""Add profile_catalog_syncs, a cache of each database's table list

One new table, nothing existing changes shape. The catalog page used to read
every database's table list live from Snowflake on every visit; this is where
that read is cached, refreshed by an explicit sync or once automatically on
a database's first-ever catalog request.

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "profile_catalog_syncs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("database_id", sa.String(), sa.ForeignKey("databases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("readable", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("tables", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("synced_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    # `unique=True, index=True` on the model's `database_id` column produces a
    # single unique index, not a separate constraint plus a plain one.
    op.create_index("ix_profile_catalog_syncs_database_id", "profile_catalog_syncs", ["database_id"], unique=True)


def downgrade() -> None:
    op.drop_table("profile_catalog_syncs")
