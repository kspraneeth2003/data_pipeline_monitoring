"""Add run explanation

The short "what is off" box on a run (checks/explain.py). A column of its own
rather than a key inside `metrics`: metrics are what the engine measured, and
this is prose about them, written afterwards and possibly by a model.

No backfill. Existing runs keep a null explanation and the UI shows their
engine message instead, which is what they always showed. Generating template
text for history would be possible, but the reuse and cooldown decisions read
the previous run's explanation, and a backfilled one would claim a model was
or was not asked when neither happened.

Revision ID: f2b7c4d9e1a3
Revises: d3a71f60c8b2
Create Date: 2026-09-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f2b7c4d9e1a3"
down_revision: Union[str, Sequence[str], None] = "d3a71f60c8b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "check_runs",
        sa.Column("explanation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("check_runs", "explanation")
