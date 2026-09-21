import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
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
    BRONZE_TO_SILVER_PARITY = "BRONZE_TO_SILVER_PARITY"


class RunStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    ERROR = "ERROR"


class TicketStatus(str, enum.Enum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"


class IncidentState(str, enum.Enum):
    """Where an incident stands. This is the agent's vocabulary, not the
    engine's - a run is PASSED/FAILED/ERROR and nothing else, deliberately.

    WARNING has no corresponding run status on purpose. A check that is
    passing but degrading - null rate climbing toward its tolerance, lag
    trending up - is not a failure, and making the engine emit it would mean
    teaching a deterministic comparison to have opinions about trends. The
    trend lives across runs, so it is read across runs.
    """

    OPEN = "OPEN"
    WARNING = "WARNING"
    CLEARED = "CLEARED"
    # Flapping, or known-noisy. Deliberately quiet, and says so out loud -
    # silence that is never explained is indistinguishable from a bug.
    SUPPRESSED = "SUPPRESSED"


class IncidentEventKind(str, enum.Enum):
    OPENED = "OPENED"
    COMMENT = "COMMENT"
    ESCALATED = "ESCALATED"
    REOPENED = "REOPENED"
    CLEARED = "CLEARED"
    SUPPRESSED = "SUPPRESSED"


class CheckOrigin(str, enum.Enum):
    """Who authored a check, which is the consent boundary for rewriting it.

    A DERIVED check is the maintenance agent's to update when the DDL moves.
    A HUMAN check is not: someone wrote intent rather than implementation, and
    that is exactly what catches the bugs derivation cannot. The seeded
    inventory check is the standing example - it fails on the WAREHOUSE_ID
    mis-mapping precisely because a person wrote what the data should be,
    while the check derived from the MERGE encodes the MERGE's own mistake
    and passes.
    """

    DERIVED = "DERIVED"
    AGENT = "AGENT"
    HUMAN = "HUMAN"


class TicketPriority(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Project(Base):
    """A data product: the databases that together serve one business domain.

    A project is not itself a database - it holds them. Customer 360 spans the
    CRM source, the billing source and the gold 360 database; the project is
    what makes those one thing.

    Connectors stay workspace-level rather than living under a project, since a
    single Snowflake account serves several projects.
    """

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=cuid)
    slug: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Where this project's pipeline is defined as code. Set when the project was
    # created by ingesting a repository; null for hand-built projects.
    #
    # `repo_commit` is the commit the project was derived from, not a live
    # pointer - it records what the proposals were read out of, so a later
    # "these checks no longer match the repo" is answerable.
    repo_url: Mapped[str | None] = mapped_column(String, nullable=True)
    repo_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    repo_commit: Mapped[str | None] = mapped_column(String, nullable=True)
    repo_path: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    databases: Mapped[list["Database"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Database.name"
    )
    connectors: Mapped[list["Connector"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Connector.name"
    )

    @property
    def checks(self) -> list["Check"]:
        return [check for database in self.databases for check in database.checks]


class Database(Base):
    """One database inside a project, reached through a connector.

    A project is a data product; the databases are where its data actually
    lives. Checks hang off a database rather than off the project directly, so
    "what is wrong" narrows to a concrete place.

    A check is *anchored* to the database holding its primary object, but may
    still reference objects in sibling databases - a silver-vs-gold parity check
    legitimately spans two. Anchoring keeps every check in exactly one place in
    the tree while leaving cross-database comparisons possible.
    """

    __tablename__ = "databases"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=cuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"))

    # The database name as the warehouse knows it, e.g. DPM_SRC_CRM.
    name: Mapped[str] = mapped_column(String)
    slug: Mapped[str] = mapped_column(String, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Schema name -> the file in the project's repository that defines it, e.g.
    # {"BRONZE": "snowflake/dpm_src_crm/bronze.sql"}. This is the per-project
    # replacement for the hardcoded map in rca/object_repo_map.py: it is what
    # lets RCA attribute a failing object to a commit and an author.
    repo_paths: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    project: Mapped["Project"] = relationship(back_populates="databases")
    connector: Mapped["Connector"] = relationship()
    checks: Mapped[list["Check"]] = relationship(
        back_populates="database", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("project_id", "slug", name="uq_databases_project_slug"),)


class Connector(Base):
    """A connection to a warehouse, owned by one project.

    Project-scoped rather than workspace-scoped: connecting is part of setting a
    project up, not a separate administrative step done somewhere else first.
    The cost is that two projects on the same account each hold their own
    credentials - deliberate, since it keeps a project self-contained and means
    deleting one can never break another.
    """

    __tablename__ = "connectors"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=cuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String)
    type: Mapped[str] = mapped_column(String)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    project: Mapped["Project"] = relationship(back_populates="connectors")
    checks: Mapped[list["Check"]] = relationship(back_populates="connector", foreign_keys="Check.connector_id")

    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_connectors_project_name"),)


class Check(Base):
    __tablename__ = "checks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=cuid)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(String)
    schedule: Mapped[str] = mapped_column(String)
    enabled: Mapped[bool] = mapped_column(default=True)

    database_id: Mapped[str] = mapped_column(ForeignKey("databases.id", ondelete="CASCADE"), index=True)

    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id"))
    secondary_connector_id: Mapped[str | None] = mapped_column(ForeignKey("connectors.id"), nullable=True)

    config: Mapped[dict] = mapped_column(JSONB, default=dict)

    # Provenance. Without it the maintenance agent has no way to tell a check
    # it may rewrite from one a person wrote by hand, and "rewrite everything"
    # would destroy the intent that makes hand-written checks worth having.
    origin: Mapped[str] = mapped_column(
        String, default=CheckOrigin.HUMAN.value, server_default=CheckOrigin.HUMAN.value
    )
    # What this check was derived from, when it was derived: the MERGE target,
    # the file, and the commit. This is what lets a change to that file be
    # traced forward to the checks it invalidates.
    derived_from: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    derived_at_commit: Mapped[str | None] = mapped_column(String, nullable=True)
    # Set on any edit through the UI or API. A derived check a person has
    # since touched is never silently rewritten - it gets a proposal instead.
    human_edited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    database: Mapped["Database"] = relationship(back_populates="checks")
    connector: Mapped["Connector"] = relationship(foreign_keys=[connector_id], back_populates="checks")
    secondary_connector: Mapped["Connector | None"] = relationship(foreign_keys=[secondary_connector_id])
    runs: Mapped[list["CheckRun"]] = relationship(back_populates="check", cascade="all, delete-orphan")
    incidents: Mapped[list["Incident"]] = relationship(
        back_populates="check", cascade="all, delete-orphan"
    )

    @property
    def agent_may_rewrite(self) -> bool:
        """Whether the maintenance agent may apply a change without review."""
        return self.origin != CheckOrigin.HUMAN.value and self.human_edited_at is None


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
    # No cascade delete: the ticket belongs to the incident now, and deleting
    # the run that happened to open it must not take the ticket with it.
    ticket: Mapped["Ticket | None"] = relationship(back_populates="check_run", uselist=False)


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


class Incident(Base):
    """One thing going wrong, across however many runs it takes to fix.

    This is the unit the reporting agent reasons about, and the reason it
    exists is that a ticket bound to a single `CheckRun` cannot express
    "still failing" or "recovered" - there is nowhere to put the second
    observation. That is why every failed run used to file a fresh ticket
    (PLAN.md FR10): the data model had no place to stand where two failures
    of the same check were the same event.

    A run is evidence about an incident. The incident is what fails, warns,
    clears, and gets escalated when nobody responds to it.
    """

    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=cuid)
    check_id: Mapped[str] = mapped_column(ForeignKey("checks.id", ondelete="CASCADE"), index=True)

    state: Mapped[str] = mapped_column(String, default=IncidentState.OPEN.value, index=True)
    severity: Mapped[str] = mapped_column(String, default=TicketPriority.MEDIUM.value)
    title: Mapped[str] = mapped_column(Text)
    # The agent's current understanding, rewritten as the incident develops.
    # Distinct from the RCA on any one run: that is a snapshot, this is the
    # running account.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    opened_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    first_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("check_runs.id", ondelete="SET NULL"), nullable=True
    )
    last_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("check_runs.id", ondelete="SET NULL"), nullable=True
    )

    failure_count: Mapped[int] = mapped_column(Integer, default=1)
    # Consecutive passes since the last failure. An incident clears on a
    # threshold rather than on the first pass, because one green run of a
    # flapping check is not a recovery.
    consecutive_passes: Mapped[int] = mapped_column(Integer, default=0)
    # How many times the agent has escalated for lack of response. Kept so
    # escalation can back off instead of nagging every tick forever.
    escalation_count: Mapped[int] = mapped_column(Integer, default=0)
    last_escalated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Groups incidents the agent believes share one upstream cause, so eight
    # checks failing on one dropped task read as one event with eight
    # symptoms rather than eight unrelated pages.
    correlation_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    # "agent" or "rule" - which triage path decided this. Worth recording:
    # when the agent is unavailable the rules still run, and a reader needs to
    # know which kind of judgement they are looking at.
    triage_source: Mapped[str] = mapped_column(String, default="rule")

    check: Mapped["Check"] = relationship(back_populates="incidents")
    events: Mapped[list["IncidentEvent"]] = relationship(
        back_populates="incident",
        cascade="all, delete-orphan",
        order_by="IncidentEvent.created_at",
    )
    ticket: Mapped["Ticket | None"] = relationship(
        back_populates="incident", cascade="all, delete-orphan", uselist=False
    )

    @property
    def is_active(self) -> bool:
        return self.state in (IncidentState.OPEN.value, IncidentState.WARNING.value)


class IncidentEvent(Base):
    """One thing the agent said or did about an incident.

    The comment stream is the product: "still failing, 3rd run, 37 -> 412
    keys" is the output a person acts on, and it only means anything as a
    sequence. Each event also records the run that prompted it, so a claim in
    a comment can be traced back to the numbers behind it.
    """

    __tablename__ = "incident_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=cuid)
    incident_id: Mapped[str] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    check_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("check_runs.id", ondelete="SET NULL"), nullable=True
    )

    kind: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String, default="rule")
    # The id this comment got in the external tracker, once synced. Null for
    # an event that never left the app - which is every event while ticketing
    # is simulated.
    external_ref: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    incident: Mapped["Incident"] = relationship(back_populates="events")


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=cuid)
    # The incident is the ticket's subject. `check_run_id` is kept as the run
    # that opened it - useful provenance, no longer the identity - and is
    # nullable because an incident can outlive the run that started it.
    incident_id: Mapped[str | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), unique=True, nullable=True
    )
    check_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("check_runs.id", ondelete="SET NULL"), nullable=True
    )

    key: Mapped[str] = mapped_column(String, unique=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String, default=TicketPriority.MEDIUM.value)
    assignee: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default=TicketStatus.TODO.value)

    # Set when the ticket lives in a real tracker rather than the in-app
    # board. `external_key` is the tracker's own key (DATA-417), which is not
    # the same as `key` - that one is ours and stays stable even if the
    # tracker is swapped.
    external_key: Mapped[str | None] = mapped_column(String, nullable=True)
    external_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    incident: Mapped["Incident | None"] = relationship(back_populates="ticket")
    check_run: Mapped["CheckRun | None"] = relationship(back_populates="ticket")
