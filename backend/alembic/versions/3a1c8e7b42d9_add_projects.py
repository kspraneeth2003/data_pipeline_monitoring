"""Add projects and scope checks to them

Existing installs already have live checks/runs/tickets, so this migration
backfills rather than resets: it creates the table, adds a nullable FK, moves
every existing check into an "Unsorted" project, and only then enforces NOT
NULL. Nothing is dropped and no run history is lost.

Revision ID: 3a1c8e7b42d9
Revises: 2270c1f41171
"""

from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

revision: str = "3a1c8e7b42d9"
down_revision: Union[str, None] = "2270c1f41171"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_slug", "projects", ["slug"], unique=True)

    op.add_column("checks", sa.Column("project_id", sa.String(), nullable=True))

    # Backfill: park pre-existing checks somewhere visible rather than inventing
    # a taxonomy for them. "Unsorted" reads as a prompt to organize, and the
    # project page lets them be moved.
    connection = op.get_bind()
    orphan_count = connection.execute(
        sa.text("SELECT COUNT(*) FROM checks WHERE project_id IS NULL")
    ).scalar()

    if orphan_count:
        fallback_id = uuid.uuid4().hex
        connection.execute(
            sa.text(
                "INSERT INTO projects (id, slug, name, description) "
                "VALUES (:id, :slug, :name, :description)"
            ),
            {
                "id": fallback_id,
                "slug": "unsorted",
                "name": "Unsorted",
                "description": "Checks that existed before projects were introduced.",
            },
        )
        connection.execute(
            sa.text("UPDATE checks SET project_id = :id WHERE project_id IS NULL"),
            {"id": fallback_id},
        )

    op.alter_column("checks", "project_id", nullable=False)
    op.create_index("ix_checks_project_id", "checks", ["project_id"])
    op.create_foreign_key(
        "fk_checks_project_id", "checks", "projects", ["project_id"], ["id"], ondelete="CASCADE"
    )


def downgrade() -> None:
    op.drop_constraint("fk_checks_project_id", "checks", type_="foreignkey")
    op.drop_index("ix_checks_project_id", table_name="checks")
    op.drop_column("checks", "project_id")
    op.drop_index("ix_projects_slug", table_name="projects")
    op.drop_table("projects")
