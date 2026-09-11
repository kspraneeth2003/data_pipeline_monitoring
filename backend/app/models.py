import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def cuid() -> str:
    return uuid.uuid4().hex


class ConnectorType(str, enum.Enum):
    SNOWFLAKE = "SNOWFLAKE"
    POSTGRES = "POSTGRES"


class CheckType(str, enum.Enum):
    ROW_COUNT = "ROW_COUNT"
    FRESHNESS = "FRESHNESS"
    NULL_RATE = "NULL_RATE"
    SCHEMA_DRIFT = "SCHEMA_DRIFT"
    CROSS_SOURCE_PARITY = "CROSS_SOURCE_PARITY"


class RunStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    ERROR = "ERROR"


class TicketStatus(str, enum.Enum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"


class TicketPriority(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Connector(Base):
    __tablename__ = "connectors"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=cuid)
    name: Mapped[str] = mapped_column(String, unique=True)
    type: Mapped[str] = mapped_column(String)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    checks: Mapped[list["Check"]] = relationship(back_populates="connector", foreign_keys="Check.connector_id")


class Check(Base):
    __tablename__ = "checks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=cuid)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(String)
    schedule: Mapped[str] = mapped_column(String)
    enabled: Mapped[bool] = mapped_column(default=True)

    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"))
    secondary_connector_id: Mapped[str | None] = mapped_column(ForeignKey("connectors.id"), nullable=True)

    config: Mapped[dict] = mapped_column(JSONB, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    connector: Mapped["Connector"] = relationship(foreign_keys=[connector_id], back_populates="checks")
    secondary_connector: Mapped["Connector | None"] = relationship(foreign_keys=[secondary_connector_id])
    runs: Mapped[list["CheckRun"]] = relationship(back_populates="check", cascade="all, delete-orphan")


class CheckRun(Base):
    __tablename__ = "check_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=cuid)
    check_id: Mapped[str] = mapped_column(ForeignKey("checks.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String, default=RunStatus.RUNNING.value)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    check: Mapped["Check"] = relationship(back_populates="runs")
    rca: Mapped["RcaResult | None"] = relationship(back_populates="check_run", cascade="all, delete-orphan", uselist=False)
    ticket: Mapped["Ticket | None"] = relationship(back_populates="check_run", cascade="all, delete-orphan", uselist=False)


class RcaResult(Base):
    __tablename__ = "rca_results"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=cuid)
    check_run_id: Mapped[str] = mapped_column(ForeignKey("check_runs.id", ondelete="CASCADE"), unique=True)

    summary: Mapped[str] = mapped_column(Text)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    next_steps: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_owner: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    check_run: Mapped["CheckRun"] = relationship(back_populates="rca")


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=cuid)
    check_run_id: Mapped[str] = mapped_column(ForeignKey("check_runs.id", ondelete="CASCADE"), unique=True)

    key: Mapped[str] = mapped_column(String, unique=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String, default=TicketPriority.MEDIUM.value)
    assignee: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default=TicketStatus.TODO.value)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    check_run: Mapped["CheckRun"] = relationship(back_populates="ticket")
