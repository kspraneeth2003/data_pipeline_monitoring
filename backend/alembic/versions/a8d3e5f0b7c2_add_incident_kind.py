"""Add incident kind

A check that cannot reach its warehouse is not a finding about the data, and
opening a data incident per check for one expired password files one Jira
issue per check. `kind` separates the two: DATA incidents come from checks,
ACCESS incidents from a connection DPM cannot use. See IncidentKind.

Every existing incident was opened by a failing or erroring run, so DATA is
what they are - the server default states that rather than guessing.

Revision ID: a8d3e5f0b7c2
Revises: f2b7c4d9e1a3
Create Date: 2026-09-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a8d3e5f0b7c2"
down_revision: Union[str, Sequence[str], None] = "f2b7c4d9e1a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "incidents",
        sa.Column("kind", sa.String(), nullable=False, server_default="DATA"),
    )


def downgrade() -> None:
    op.drop_column("incidents", "kind")
