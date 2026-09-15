"""Move connectors under projects

Connecting a warehouse is part of setting a project up, not a separate
administrative step performed elsewhere first. Each project now owns its
connections.

A connector shared by two projects cannot simply be reassigned to one of them,
so it is cloned: the first project keeps the original row, every other project
using it gets a copy (same encrypted config, so no credential is re-entered or
decrypted here), and that project's databases and checks are repointed at the
copy. Connectors no project uses are dropped.

Revision ID: 7e4b0c92aa15
Revises: 5c2f9a1d77e4
"""

from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

revision: str = "7e4b0c92aa15"
down_revision: Union[str, None] = "5c2f9a1d77e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("connectors", sa.Column("project_id", sa.String(), nullable=True))
    # Dropped up front: cloning a shared connector into a second project would
    # otherwise collide on the globally-unique name before the new per-project
    # constraint exists.
    op.drop_constraint("connectors_name_key", "connectors", type_="unique")

    connection = op.get_bind()

    # Every (project, connector) pair actually in use, via that project's databases.
    pairs = connection.execute(
        sa.text(
            "SELECT DISTINCT d.project_id, d.connector_id "
            "FROM databases d ORDER BY d.project_id, d.connector_id"
        )
    ).all()

    claimed: set[str] = set()

    for project_id, connector_id in pairs:
        if connector_id not in claimed:
            # First project to use it keeps the original row.
            connection.execute(
                sa.text("UPDATE connectors SET project_id = :p WHERE id = :c"),
                {"p": project_id, "c": connector_id},
            )
            claimed.add(connector_id)
            continue

        # Copy the row verbatim, config included - the encrypted secret travels
        # with it, so nothing is decrypted or re-entered here.
        clone_id = uuid.uuid4().hex
        connection.execute(
            sa.text(
                "INSERT INTO connectors (id, project_id, name, type, config, comment) "
                "SELECT :id, :project_id, name, type, config, comment "
                "FROM connectors WHERE id = :src"
            ),
            {"id": clone_id, "project_id": project_id, "src": connector_id},
        )

        connection.execute(
            sa.text(
                "UPDATE databases SET connector_id = :new WHERE project_id = :p AND connector_id = :old"
            ),
            {"new": clone_id, "p": project_id, "old": connector_id},
        )
        connection.execute(
            sa.text(
                "UPDATE checks SET connector_id = :new WHERE connector_id = :old AND database_id IN "
                "(SELECT id FROM databases WHERE project_id = :p)"
            ),
            {"new": clone_id, "p": project_id, "old": connector_id},
        )
        connection.execute(
            sa.text(
                "UPDATE checks SET secondary_connector_id = :new WHERE secondary_connector_id = :old "
                "AND database_id IN (SELECT id FROM databases WHERE project_id = :p)"
            ),
            {"new": clone_id, "p": project_id, "old": connector_id},
        )

    # Anything still unclaimed belongs to no project and nothing references it.
    connection.execute(sa.text("DELETE FROM connectors WHERE project_id IS NULL"))

    op.alter_column("connectors", "project_id", nullable=False)
    op.create_index("ix_connectors_project_id", "connectors", ["project_id"])
    op.create_foreign_key(
        "fk_connectors_project_id", "connectors", "projects", ["project_id"], ["id"], ondelete="CASCADE"
    )

    # Names only have to be unique inside a project now, so two projects can
    # each call their connection "snowflake".
    op.create_unique_constraint("uq_connectors_project_name", "connectors", ["project_id", "name"])


def downgrade() -> None:
    op.drop_constraint("uq_connectors_project_name", "connectors", type_="unique")
    op.create_unique_constraint("connectors_name_key", "connectors", ["name"])
    op.drop_constraint("fk_connectors_project_id", "connectors", type_="foreignkey")
    op.drop_index("ix_connectors_project_id", table_name="connectors")
    op.drop_column("connectors", "project_id")
