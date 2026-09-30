"""Add column profiling and anomaly detection

Four new tables and nothing else: no existing table gains or loses a column,
so this migration cannot change how any check behaves.

- `profile_targets`   which tables are profiled, on what schedule
- `profile_runs`      one row per profiling pass
- `column_profiles`   per column per run: counts, ratios, bounds, lengths
- `profile_anomalies` findings and history anomalies, per run

Nothing is backfilled. A profile's history is observations of the warehouse
over time, and there is no way to observe the past - inventing a baseline
here would teach the detector a normal that was never measured.

Revision ID: f1a2b3c4d5e6
Revises: d3a71f60c8b2
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "d3a71f60c8b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "profile_targets",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("database_id", sa.String(), sa.ForeignKey("databases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("object", sa.String(), nullable=False),
        sa.Column("schedule", sa.String(), nullable=False, server_default="0 * * * *"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("database_id", "object", name="uq_profile_targets_database_object"),
    )
    op.create_index("ix_profile_targets_database_id", "profile_targets", ["database_id"])

    op.create_table(
        "profile_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("target_id", sa.String(), sa.ForeignKey("profile_targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("column_count", sa.Integer(), nullable=True),
        sa.Column("anomaly_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("message", sa.Text(), nullable=True),
    )
    op.create_index("ix_profile_runs_target_id", "profile_runs", ["target_id"])

    op.create_table(
        "column_profiles",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("profile_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_id", sa.String(), sa.ForeignKey("profile_targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("column_name", sa.String(), nullable=False),
        sa.Column("data_type", sa.String(), nullable=False),
        sa.Column("family", sa.String(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("null_count", sa.Integer(), nullable=False),
        sa.Column("distinct_count", sa.Integer(), nullable=True),
        sa.Column("blank_count", sa.Integer(), nullable=True),
        sa.Column("min_numeric", sa.Float(), nullable=True),
        sa.Column("max_numeric", sa.Float(), nullable=True),
        sa.Column("mean_numeric", sa.Float(), nullable=True),
        sa.Column("min_value", sa.Text(), nullable=True),
        sa.Column("max_value", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_column_profiles_run_id", "column_profiles", ["run_id"])
    op.create_index("ix_column_profiles_target_id", "column_profiles", ["target_id"])

    op.create_table(
        "profile_anomalies",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("target_id", sa.String(), sa.ForeignKey("profile_targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.String(), sa.ForeignKey("profile_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("column_name", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("metric", sa.String(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("observed", sa.Float(), nullable=True),
        sa.Column("expected", sa.Float(), nullable=True),
        sa.Column("lower", sa.Float(), nullable=True),
        sa.Column("upper", sa.Float(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_profile_anomalies_target_id", "profile_anomalies", ["target_id"])
    op.create_index("ix_profile_anomalies_run_id", "profile_anomalies", ["run_id"])


def downgrade() -> None:
    op.drop_table("profile_anomalies")
    op.drop_table("column_profiles")
    op.drop_table("profile_runs")
    op.drop_table("profile_targets")
