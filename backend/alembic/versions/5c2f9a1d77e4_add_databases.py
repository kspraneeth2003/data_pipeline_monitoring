"""Insert databases between projects and checks

A project is a data product, not a database - it holds several. This adds the
middle level and re-anchors checks onto it.

The backfill is derived, not guessed: every check config names its primary
object fully qualified (DPM_SRC_CRM.SILVER.CUSTOMERS), so the database is the
first segment. Each distinct database found becomes a Database row inside the
project that check already belonged to, and the check moves onto it. Nothing is
dropped and no run history is lost.

Revision ID: 5c2f9a1d77e4
Revises: 3a1c8e7b42d9
"""

from typing import Sequence, Union
import re
import uuid

from alembic import op
import sqlalchemy as sa

revision: str = "5c2f9a1d77e4"
down_revision: Union[str, None] = "3a1c8e7b42d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The key holding a check's primary object differs per check type.
PRIMARY_OBJECT_KEYS = ("object", "bronzeObject", "primaryObject")


def _database_of(config: dict) -> str | None:
    for key in PRIMARY_OBJECT_KEYS:
        value = config.get(key)
        if isinstance(value, str) and "." in value:
            return value.split(".")[0].strip('"').upper()
    # CROSS_SOURCE_PARITY carries raw SQL rather than an object reference; fall
    # back to the first fully-qualified name that appears in the query.
    for key in ("primaryQuery", "secondaryQuery"):
        value = config.get(key)
        if isinstance(value, str):
            match = re.search(r"\b([A-Z_][A-Z0-9_]*)\.[A-Z_][A-Z0-9_]*\.[A-Z_][A-Z0-9_]*", value.upper())
            if match:
                return match.group(1)
    return None


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "database"


def upgrade() -> None:
    op.create_table(
        "databases",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("connector_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connector_id"], ["connectors.id"]),
        sa.UniqueConstraint("project_id", "slug", name="uq_databases_project_slug"),
    )
    op.create_index("ix_databases_project_id", "databases", ["project_id"])
    op.create_index("ix_databases_slug", "databases", ["slug"])

    op.add_column("checks", sa.Column("database_id", sa.String(), nullable=True))

    connection = op.get_bind()
    checks = connection.execute(
        sa.text("SELECT id, project_id, connector_id, config FROM checks")
    ).mappings().all()

    # (project_id, database name) -> database id
    created: dict[tuple[str, str], str] = {}

    for check in checks:
        name = _database_of(check["config"] or {}) or "UNKNOWN"
        key = (check["project_id"], name)

        if key not in created:
            existing = connection.execute(
                sa.text("SELECT id FROM databases WHERE project_id = :p AND name = :n"),
                {"p": check["project_id"], "n": name},
            ).scalar()
            if existing:
                created[key] = existing
            else:
                database_id = uuid.uuid4().hex
                connection.execute(
                    sa.text(
                        "INSERT INTO databases (id, project_id, connector_id, name, slug) "
                        "VALUES (:id, :project_id, :connector_id, :name, :slug)"
                    ),
                    {
                        "id": database_id,
                        "project_id": check["project_id"],
                        "connector_id": check["connector_id"],
                        "name": name,
                        "slug": _slugify(name),
                    },
                )
                created[key] = database_id

        connection.execute(
            sa.text("UPDATE checks SET database_id = :d WHERE id = :c"),
            {"d": created[key], "c": check["id"]},
        )

    op.alter_column("checks", "database_id", nullable=False)
    op.create_index("ix_checks_database_id", "checks", ["database_id"])
    op.create_foreign_key(
        "fk_checks_database_id", "checks", "databases", ["database_id"], ["id"], ondelete="CASCADE"
    )

    # The project is now reached through the database, so a direct link would be
    # a second source of truth that can disagree.
    op.drop_constraint("fk_checks_project_id", "checks", type_="foreignkey")
    op.drop_index("ix_checks_project_id", table_name="checks")
    op.drop_column("checks", "project_id")


def downgrade() -> None:
    op.add_column("checks", sa.Column("project_id", sa.String(), nullable=True))
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE checks SET project_id = d.project_id FROM databases d WHERE d.id = checks.database_id"
        )
    )
    op.alter_column("checks", "project_id", nullable=False)
    op.create_index("ix_checks_project_id", "checks", ["project_id"])
    op.create_foreign_key(
        "fk_checks_project_id", "checks", "projects", ["project_id"], ["id"], ondelete="CASCADE"
    )

    op.drop_constraint("fk_checks_database_id", "checks", type_="foreignkey")
    op.drop_index("ix_checks_database_id", table_name="checks")
    op.drop_column("checks", "database_id")
    op.drop_index("ix_databases_slug", table_name="databases")
    op.drop_index("ix_databases_project_id", table_name="databases")
    op.drop_table("databases")
