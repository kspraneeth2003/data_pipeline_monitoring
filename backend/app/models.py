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
    # Advanced: the history a type-2 dimension holds, rather than whether
    # its rows arrived. Parity cannot see this class of fault at all.
    SCD2_INTEGRITY = "SCD2_INTEGRITY"


class CheckStage(str, enum.Enum):
    """The pipeline hop a check watches, and the project page's top-level tabs.

    Three of these name a movement between layers; the fourth is everything
    asserted about one table on its own. That asymmetry is deliberate - it is
    the only grouping under which a check belongs to exactly one tab. Origin
    (derived, agent, human) is a second axis and stays a filter, because a
    hand-written parity check is both, and tabs that overlap either duplicate
    rows or hide them.

    DATA_QUALITY is also the fallback for anything whose hop cannot be read
    off its config. See `checks/stage.py` for why that beats an "unknown" bin.
    """

    STG_TO_BRONZE = "STG_TO_BRONZE"
    BRONZE_TO_SILVER = "BRONZE_TO_SILVER"
    SILVER_TO_GOLD = "SILVER_TO_GOLD"
    DATA_QUALITY = "DATA_QUALITY"


class RunStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    ERROR = "ERROR"


class IncidentSeverity(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


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
    # What this check asserts, in at most two lines of plain language.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Why it is worth asserting: the invariant relied on, where that invariant
    # came from (this MERGE, this NOT NULL, this SCD2 shape), and what a
    # failure would mean. Promoted out of `derived_from` - it lived there as an
    # incidental key, so only derived checks had one and nothing rendered it,
    # which left every hand-written check a red square with no explanation.
    # The third thing a check owes its reader, the SQL, is built on demand from
    # `config` by `checks/sql.py` rather than stored, so it cannot go stale.
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(String)
    schedule: Mapped[str] = mapped_column(String)
    enabled: Mapped[bool] = mapped_column(default=True)

    # Which hop this check watches, and so which project tab it appears under.
    # Derived from the objects its config compares (`checks/stage.py`) on every
    # write, unless `stage_locked` says a person put it somewhere on purpose.
    stage: Mapped[str] = mapped_column(
        String,
        default=CheckStage.DATA_QUALITY.value,
        server_default=CheckStage.DATA_QUALITY.value,
        index=True,
    )
    stage_locked: Mapped[bool] = mapped_column(default=False, server_default="false")

    # Kept at the top of its tab. Workspace-wide rather than per-viewer: there
    # is no user table to hang a personal pin off, so a pin is a statement about
    # the check ("this is the one we are watching"), not about who is looking.
    pinned: Mapped[bool] = mapped_column(default=False, server_default="false")

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
    revisions: Mapped[list["CheckRevision"]] = relationship(
        back_populates="check", cascade="all, delete-orphan"
    )
    versions: Mapped[list["CheckVersion"]] = relationship(
        back_populates="check", cascade="all, delete-orphan", order_by="CheckVersion.version"
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


class RevisionKind(str, enum.Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    RETIRE = "RETIRE"


class RevisionStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    # Applied by the agent without review, which it may do only for a check
    # it authored and nobody has since edited.
    AUTO_APPLIED = "AUTO_APPLIED"


class CheckRevision(Base):
    """A proposed change to a check, because the pipeline's definition moved.

    The maintenance agent writes these; it does not edit checks directly.
    That is the whole safety model: the agent's output is a diff a person can
    read, and only a check the agent itself derived - and that nobody has
    since edited by hand - is applied without someone looking.

    Kept even after review. A rejected revision is the record that somebody
    considered this change and decided against it, which is what stops the
    agent proposing the same thing every time the detector fires.
    """

    __tablename__ = "check_revisions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=cuid)
    # Null for a CREATE: there is no check yet.
    check_id: Mapped[str | None] = mapped_column(
        ForeignKey("checks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    database_id: Mapped[str] = mapped_column(
        ForeignKey("databases.id", ondelete="CASCADE"), index=True
    )

    kind: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(
        String, default=RevisionStatus.PENDING.value, index=True
    )

    # The check as it stands, so the diff survives a later edit to the check
    # itself and a reviewer sees what the agent actually compared against.
    current_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    proposed_name: Mapped[str | None] = mapped_column(String, nullable=True)
    proposed_type: Mapped[str | None] = mapped_column(String, nullable=True)
    proposed_schedule: Mapped[str | None] = mapped_column(String, nullable=True)
    proposed_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Why, in the agent's words, and what triggered it.
    reason: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    detected_change: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    triggered_by_commit: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    check: Mapped["Check | None"] = relationship(back_populates="revisions")
    database: Mapped["Database"] = relationship()


class CheckVersion(Base):
    """Every state a check's logic has been in, and the SQL that state produced.

    Two things needed this table. Editing a check used to overwrite it in
    place, so "what did this assert last Tuesday" was unanswerable - and the
    fields people reach for first, the parity filters and the per-side cast
    expressions, are exactly the ones where a well-meant edit silently changes
    what is being asserted. `CheckRevision` looks like it would cover this but
    does not: it is the agent's proposal queue, and a human edit never went
    through it.

    The second is the stored SQL. `sql.py` still generates statements on read,
    and that stays the source of truth for what runs - a stored copy consulted
    at execution time would be a second truth that goes stale the first time a
    builder changes. What is stored here is a *snapshot*: the SQL this config
    rendered to at the moment it was saved. That is a record of the past, which
    cannot go stale the way a live copy can, and it is what makes a version
    diff readable - two configs side by side say much less than two queries do.

    So: config changes -> a new version is written with freshly rendered SQL,
    automatically. The reverse, editing SQL and having config follow, is not
    implemented and is not a matter of wiring: it needs arbitrary SQL parsed
    back into structured fields, which only works for SQL the generator itself
    produced and fails silently the moment someone hand-edits a predicate.
    """

    __tablename__ = "check_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=cuid)
    check_id: Mapped[str] = mapped_column(ForeignKey("checks.id", ondelete="CASCADE"), index=True)

    # Monotonic per check, starting at 1. A number rather than a timestamp
    # because it is what the UI labels a version with and what a restore names.
    version: Mapped[int] = mapped_column(Integer)

    # The check as it stood at this version. Denormalised on purpose: a version
    # that read through to the live check would change meaning when the check
    # did, which is the whole thing this table exists to prevent.
    name: Mapped[str] = mapped_column(String)
    type: Mapped[str] = mapped_column(String)
    schedule: Mapped[str] = mapped_column(String)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)

    # The rendered statements, and their concatenation for display and diffing.
    # `statements_error` is populated instead when the config could not build -
    # kept rather than refused, since a version that would have errored is
    # itself worth being able to look back at.
    statements: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    sql_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    statements_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Who caused this version: "human", "agent", "ingest" or "seed". Without it
    # a history of twenty versions cannot answer the first question anyone asks
    # of it, which is whether a person or the maintenance agent did this.
    author: Mapped[str] = mapped_column(String, default="human")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    check: Mapped["Check"] = relationship(back_populates="versions")

    __table_args__ = (UniqueConstraint("check_id", "version", name="uq_check_versions_check_version"),)


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
    severity: Mapped[str] = mapped_column(String, default=IncidentSeverity.MEDIUM.value)
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

    # --- The Jira issue, which lives in Jira ---------------------------
    #
    # This app holds a reference and a cached view of it, never a copy. The
    # issue is Jira's: its status, its assignee and its comments are edited
    # by people there, and anything stored here is stale the moment they do.
    #
    # The cache exists for exactly one reason. "Nobody has responded to this"
    # is the judgement that makes escalation worth having, and answering it
    # needs to know whether the issue has moved - which is a question only
    # Jira can answer. Re-reading it on every decision would put a network
    # call in the path of every sweep, so the sweep refreshes it once and
    # the rest of the pass reads the cache.
    #
    # Null ticket_key means no issue exists: Jira is unconfigured, or filing
    # failed. The incident is still fully tracked here either way.
    ticket_key: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    ticket_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Jira's own status name ("To Do", "In Progress", a custom one). Free
    # text on purpose - workflows are per-project and an enum here would be
    # wrong on the first site that renamed a column.
    ticket_status: Mapped[str | None] = mapped_column(String, nullable=True)
    ticket_assignee: Mapped[str | None] = mapped_column(String, nullable=True)
    # When the cache was last refreshed, and when the status last actually
    # changed. The second is what "untouched for three days" is measured
    # from - `updated_at` on the incident moves for our own writes too.
    ticket_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ticket_moved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    check: Mapped["Check"] = relationship(back_populates="incidents")
    events: Mapped[list["IncidentEvent"]] = relationship(
        back_populates="incident",
        cascade="all, delete-orphan",
        order_by="IncidentEvent.created_at",
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
