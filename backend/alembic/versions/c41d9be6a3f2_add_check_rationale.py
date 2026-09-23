"""Add check rationale

Every check owes its reader a description, the logic behind it, and its SQL.
The SQL is built on demand from `config`, and the description already had a
column. The logic did not: ingestion computed a `rationale` per proposal and
then buried it inside the `derived_from` JSON blob, so only derived checks
carried one and no view ever rendered it.

The backfill lifts those buried values into the new column rather than starting
empty, since they are the only written explanation the existing derived checks
have. `derived_from` keeps its copy - it is a provenance record of what the
proposal said at the time, and rewriting history to match a column that can now
be edited would make it useless for exactly the question it answers.

Revision ID: c41d9be6a3f2
Revises: bf05f779ddae
Create Date: 2026-09-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c41d9be6a3f2"
down_revision: Union[str, Sequence[str], None] = "bf05f779ddae"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("checks", sa.Column("rationale", sa.Text(), nullable=True))
    op.execute(
        """
        UPDATE checks
           SET rationale = derived_from ->> 'rationale'
         WHERE derived_from IS NOT NULL
           AND derived_from ->> 'rationale' IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("checks", "rationale")
