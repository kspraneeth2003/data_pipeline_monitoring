"""Add check stage, pinning and version history

The project page now groups checks by the pipeline hop they watch rather than
by the database they are stored under, which needs a `stage` on the row: see
`checks/stage.py` for why deriving it in the frontend was rejected.

`check_versions` is new, and is two things at once. It is the history a human
edit never had - `check_revisions` is the maintenance agent's proposal queue,
and an edit through the UI overwrote the check in place with no before-image.
And it is where rendered SQL is stored, as a snapshot per version. Live SQL is
still generated from config on read and that stays the source of truth; what is
stored here is what the config rendered to at the moment it was saved, which is
a record of the past and so cannot go stale.

The backfill writes version 1 for every existing check, authored "backfill", so
a check that has never been edited still has a first version to diff against.
Its `sql_text` is deliberately left null rather than rendered here: rendering
would mean importing the whole app into a migration, and a snapshot invented
now would claim to be what that config looked like at creation time when it is
only what it looks like today.

Revision ID: d3a71f60c8b2
Revises: c41d9be6a3f2
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d3a71f60c8b2"
down_revision: Union[str, Sequence[str], None] = "c41d9be6a3f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "checks",
        sa.Column("stage", sa.String(), nullable=False, server_default="DATA_QUALITY"),
    )
    op.add_column(
        "checks",
        sa.Column("stage_locked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "checks",
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index("ix_checks_stage", "checks", ["stage"])

    op.create_table(
        "check_versions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "check_id",
            sa.String(),
            sa.ForeignKey("checks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("schedule", sa.String(), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("statements", postgresql.JSONB(), nullable=True),
        sa.Column("sql_text", sa.Text(), nullable=True),
        sa.Column("statements_error", sa.Text(), nullable=True),
        sa.Column("author", sa.String(), nullable=False, server_default="human"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("check_id", "version", name="uq_check_versions_check_version"),
    )
    op.create_index("ix_check_versions_check_id", "check_versions", ["check_id"])

    # Backfill the stage of the two-object check types. Everything else stays
    # DATA_QUALITY, which is where a single-table assertion belongs anyway.
    # Written as SQL rather than by importing `derive_stage` so this migration
    # keeps working if that function's rules change later - a migration that
    # calls into live application code produces different results depending on
    # when it is run, which is the one thing a migration must not do.
    op.execute(
        """
        UPDATE checks SET stage = 'BRONZE_TO_SILVER'
        WHERE type = 'BRONZE_TO_SILVER_PARITY'
          AND UPPER(SPLIT_PART(config->>'bronzeObject', '.', 2)) = 'BRONZE'
          AND UPPER(SPLIT_PART(config->>'silverObject', '.', 2)) = 'SILVER'
        """
    )
    op.execute(
        """
        UPDATE checks SET stage = 'STG_TO_BRONZE'
        WHERE type = 'BRONZE_TO_SILVER_PARITY'
          AND UPPER(SPLIT_PART(config->>'bronzeObject', '.', 2)) IN ('STG', 'STAGE', 'STAGING', 'RAW', 'LANDING')
          AND UPPER(SPLIT_PART(config->>'silverObject', '.', 2)) = 'BRONZE'
        """
    )
    op.execute(
        """
        UPDATE checks SET stage = 'SILVER_TO_GOLD'
        WHERE type = 'BRONZE_TO_SILVER_PARITY'
          AND UPPER(SPLIT_PART(config->>'bronzeObject', '.', 2)) = 'SILVER'
          AND UPPER(SPLIT_PART(config->>'silverObject', '.', 2)) IN ('GOLD', 'MART', 'MARTS')
        """
    )
    op.execute(
        """
        UPDATE checks SET stage = 'SILVER_TO_GOLD'
        WHERE type = 'ROW_COUNT'
          AND config->>'comparisonObject' IS NOT NULL
          AND UPPER(SPLIT_PART(config->>'object', '.', 2)) = 'SILVER'
          AND UPPER(SPLIT_PART(config->>'comparisonObject', '.', 2)) IN ('GOLD', 'MART', 'MARTS')
        """
    )

    op.execute(
        """
        INSERT INTO check_versions (id, check_id, version, name, type, schedule, config, author, note, created_at)
        SELECT REPLACE(gen_random_uuid()::text, '-', ''), id, 1, name, type, schedule, config,
               'backfill', 'State when version history was introduced', created_at
        FROM checks
        """
    )


def downgrade() -> None:
    op.drop_index("ix_check_versions_check_id", table_name="check_versions")
    op.drop_table("check_versions")
    op.drop_index("ix_checks_stage", table_name="checks")
    op.drop_column("checks", "pinned")
    op.drop_column("checks", "stage_locked")
    op.drop_column("checks", "stage")
