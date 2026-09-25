"""Tables for profiling: what is profiled, each pass, each column, each finding.

Kept in their own module rather than added to `app/models.py` so the feature is
additive - nothing the check engine reads changes shape. They share `Base`, so
Alembic and `create_all` see them like any other table.
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models import Database, cuid


class ProfileRunStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    ERROR = "ERROR"


class AnomalyKind(str, enum.Enum):
    # Judged against this column's own history. Needs a baseline first.
    ANOMALY = "ANOMALY"
    # True of one run on its own - a column NULL on every row, a timestamp in
    # the future. Needs no history, which is what lets it catch a fault that
    # has been present since the first load.
    FINDING = "FINDING"
    # The column set moved between two runs.
    SCHEMA = "SCHEMA"


class AnomalySeverity(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ProfileTarget(Base):
    """One table whose columns are profiled on a schedule.

    Anchored to a database for the same reason a check is: the database holds
    the connector, and a project reaches its tables through its databases, so
    there is exactly one path from a project to a profile.
    """

    __tablename__ = "profile_targets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=cuid)
    database_id: Mapped[str] = mapped_column(ForeignKey("databases.id", ondelete="CASCADE"), index=True)
    # Fully qualified, DB.SCHEMA.TABLE, as the warehouse names it.
    object: Mapped[str] = mapped_column(String)
    # Hourly by default. A profile is one full scan of the table, and the
    # history it builds only needs to be dense enough to have a baseline -
    # profiling on the check cadence would multiply warehouse cost for no
    # sharper answer.
    schedule: Mapped[str] = mapped_column(String, default="0 * * * *", server_default="0 * * * *")
    enabled: Mapped[bool] = mapped_column(default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    database: Mapped[Database] = relationship()
    runs: Mapped[list["ProfileRun"]] = relationship(
        back_populates="target", cascade="all, delete-orphan", order_by="ProfileRun.started_at"
    )

    __table_args__ = (UniqueConstraint("database_id", "object", name="uq_profile_targets_database_object"),)


class ProfileRun(Base):
    """One profiling pass over a table: a single statement, a single snapshot."""

    __tablename__ = "profile_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=cuid)
    target_id: Mapped[str] = mapped_column(ForeignKey("profile_targets.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String, default=ProfileRunStatus.RUNNING.value)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    column_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    anomaly_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    target: Mapped[ProfileTarget] = relationship(back_populates="runs")
    columns: Mapped[list["ColumnProfile"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="ColumnProfile.ordinal"
    )
    anomalies: Mapped[list["ProfileAnomaly"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class ColumnProfile(Base):
    """What one column looked like on one run.

    `min_numeric` / `max_numeric` / `mean_numeric` mean different things per
    type family, because that is the only way to compare text safely: for
    numbers they are the values, for text they are string *lengths*, for
    dates and timestamps they are epoch seconds, and for booleans the mean is
    the share of TRUE. `min_value` / `max_value` are the display forms.
    """

    __tablename__ = "column_profiles"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=cuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("profile_runs.id", ondelete="CASCADE"), index=True)
    # Denormalised from the run so a column's history is one indexed read.
    target_id: Mapped[str] = mapped_column(ForeignKey("profile_targets.id", ondelete="CASCADE"), index=True)

    column_name: Mapped[str] = mapped_column(String)
    data_type: Mapped[str] = mapped_column(String)
    family: Mapped[str] = mapped_column(String)
    ordinal: Mapped[int] = mapped_column(Integer)

    row_count: Mapped[int] = mapped_column(Integer)
    null_count: Mapped[int] = mapped_column(Integer)
    distinct_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    blank_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_numeric: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_numeric: Mapped[float | None] = mapped_column(Float, nullable=True)
    mean_numeric: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    run: Mapped[ProfileRun] = relationship(back_populates="columns")


class ProfileAnomaly(Base):
    """Something a profile run found worth a person's attention.

    Recorded per run rather than as a long-lived incident: "active" means
    "present on the table's latest successful run", which clears itself the
    moment the data recovers without anyone having to close anything.

    Acknowledging is the one piece of state that carries forward. A column
    that is legitimately always NULL would otherwise raise the same finding
    every hour forever, and a finding that cannot be quietened is a finding
    that gets ignored - along with every real one next to it.
    """

    __tablename__ = "profile_anomalies"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=cuid)
    target_id: Mapped[str] = mapped_column(ForeignKey("profile_targets.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("profile_runs.id", ondelete="CASCADE"), index=True)

    # Null for a table-level metric such as row volume.
    column_name: Mapped[str | None] = mapped_column(String, nullable=True)
    kind: Mapped[str] = mapped_column(String)
    metric: Mapped[str] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String)

    observed: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected: Mapped[float | None] = mapped_column(Float, nullable=True)
    lower: Mapped[float | None] = mapped_column(Float, nullable=True)
    upper: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str] = mapped_column(Text)

    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    run: Mapped[ProfileRun] = relationship(back_populates="anomalies")
