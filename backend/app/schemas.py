from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ConnectorCreate(BaseModel):
    name: str
    project_id: str
    type: str = "SNOWFLAKE"
    comment: str | None = None
    config: dict[str, Any] = {}
    test_connection: bool = True


class ConnectionProbe(BaseModel):
    type: str = "SNOWFLAKE"
    config: dict[str, Any] = {}


class ConnectionProbeResult(BaseModel):
    account: str | None = None
    username: str | None = None
    role: str | None = None
    warehouse: str | None = None
    databases: list[str] = []


class ProjectSetup(BaseModel):
    """Everything a project needs, in one call: name it, connect it, pick its
    databases. Splitting this across three screens is what made setup feel like
    administration rather than getting started."""

    name: str
    description: str | None = None
    connector_name: str = "snowflake"
    connector_type: str = "SNOWFLAKE"
    config: dict[str, Any] = {}
    databases: list[str] = []


class ConnectorUpdate(BaseModel):
    name: str | None = None
    comment: str | None = None
    config: dict[str, Any] | None = None
    test_connection: bool = True


class ConnectorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    project_id: str
    type: str
    config: dict[str, Any]
    comment: str | None
    created_at: datetime
    updated_at: datetime
    checks_count: int = 0


class ProjectCreate(BaseModel):
    name: str
    slug: str | None = None
    description: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    slug: str | None = None
    description: str | None = None


class ProjectRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    slug: str
    name: str


class DatabaseCreate(BaseModel):
    name: str
    connector_id: str
    slug: str | None = None
    description: str | None = None


class DatabaseUpdate(BaseModel):
    name: str | None = None
    slug: str | None = None
    description: str | None = None
    connector_id: str | None = None


class DatabaseRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    slug: str
    name: str
    project: ProjectRef


class CheckCreate(BaseModel):
    name: str
    description: str | None = None
    rationale: str | None = None
    type: str
    schedule: str
    enabled: bool = True
    database_id: str
    connector_id: str
    secondary_connector_id: str | None = None
    config: dict[str, Any]


class CheckUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    rationale: str | None = None
    type: str | None = None
    schedule: str | None = None
    enabled: bool | None = None
    database_id: str | None = None
    connector_id: str | None = None
    secondary_connector_id: str | None = None
    config: dict[str, Any] | None = None
    pinned: bool | None = None
    # Setting this overrules the derived stage and locks it. Left unset, the
    # stage is re-derived from the config being saved.
    stage: str | None = None
    # Why this edit was made, carried onto the version it produces. Optional,
    # but a history where every entry says nothing is a list of timestamps.
    note: str | None = None


class CheckPin(BaseModel):
    pinned: bool



class ConnectorRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str


class RcaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    summary: str
    root_cause: str | None
    confidence: float | None
    evidence: dict[str, Any] | None
    next_steps: str | None
    suggested_owner: str | None
    created_at: datetime


class CheckRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    metrics: dict[str, Any] | None
    message: str | None
    rca: RcaOut | None = None


class CheckStatementOut(BaseModel):
    """One statement the check issues, and what it establishes.

    Built from `config` on read rather than stored, so it is always the SQL the
    engine would actually run. `error` is populated instead of `statements`
    when the config cannot produce one - a check that would ERROR on its first
    run, which is worth seeing before it is scheduled rather than after.
    """

    label: str
    sql: str
    connection: str


class CheckVersionOut(BaseModel):
    """One past state of a check's logic, with the SQL it rendered to then.

    `sql_text` is a snapshot taken when the version was saved, not a live
    render - that is what makes it comparable across versions. See
    `models.CheckVersion` for why it does not go stale the way a stored
    live copy would.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    version: int
    name: str
    type: str
    schedule: str
    config: dict[str, Any]
    statements: list[CheckStatementOut] | None = None
    sql_text: str | None = None
    statements_error: str | None = None
    author: str
    note: str | None = None
    created_at: datetime


class CheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    rationale: str | None
    statements: list[CheckStatementOut] = []
    statements_error: str | None = None
    type: str
    schedule: str
    enabled: bool
    stage: str
    stage_locked: bool = False
    pinned: bool = False
    origin: str
    database_id: str
    connector_id: str
    secondary_connector_id: str | None
    config: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    database: DatabaseRef
    connector: ConnectorRef
    secondary_connector: ConnectorRef | None = None
    runs: list[CheckRunOut] = []


class ProjectHealth(BaseModel):
    """Rollup shown on a project card. Ordered so the worst state wins: a
    project with one failing check reads as failing, not as "mostly passing"."""

    total_checks: int
    passing: int
    failing: int
    erroring: int
    never_run: int
    disabled: int
    open_incidents: int
    last_run_at: datetime | None
    status: str  # PASSED | FAILED | ERROR | NONE


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


class DatabaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    name: str
    description: str | None
    project_id: str
    connector_id: str
    created_at: datetime
    updated_at: datetime


class DatabaseWithHealthOut(DatabaseOut):
    connector: ConnectorRef
    health: ProjectHealth


class ProjectWithHealthOut(ProjectOut):
    health: ProjectHealth
    databases: list[DatabaseWithHealthOut] = []


# --- Repository ingestion ------------------------------------------------


class RepoIngestRequest(BaseModel):
    """Start an analysis.

    Two ways to authenticate, and the difference matters. `installation_id`
    names a GitHub App installation the user granted; the server mints a
    short-lived token for it at clone time, so no credential travels through
    the browser. `token` is the manual fallback - used for the fetch and then
    dropped, never stored.
    """

    repo_url: str
    token: str | None = None
    ref: str | None = None
    installation_id: int | None = None


class ProposedCheck(BaseModel):
    key: str
    name: str
    description: str
    rationale: str
    type: str
    schedule: str
    database: str
    config: dict[str, Any]
    source: str
    concerns: list[str] = []
    # The SQL this proposal would run, so review sees all three of description,
    # logic and statement before anything is written. A proposal whose config
    # cannot build one is a check that would ERROR on its first run, which is a
    # reason to reject it - hence the error travels with it rather than being
    # swallowed.
    statements: list[CheckStatementOut] = []
    statements_error: str | None = None


class ProposedDatabase(BaseModel):
    name: str
    description: str
    repo_paths: dict[str, str]
    tables: list[str]


class TableCoverageOut(BaseModel):
    table: str
    parity: bool
    freshness: bool
    schema_drift: bool
    gaps: list[str]


class CoverageOut(BaseModel):
    """What the rules covered and what they could not.

    Shown next to the proposal count because the two are only meaningful
    together: "12 checks" reads as success on its own, even when it came from
    a repo where the parser understood a fraction of the tables.
    """

    tables_total: int
    tables_expecting_parity: int
    tables_with_parity: int
    uncovered: list[TableCoverageOut]
    summary: str


class RepoAnalysisOut(BaseModel):
    repo_url: str
    repo_ref: str | None
    repo_commit: str | None
    repo_commit_subject: str | None
    project_name: str
    project_description: str
    databases: list[ProposedDatabase]
    checks: list[ProposedCheck]
    coverage: CoverageOut
    sql_files: list[str]
    table_count: int
    llm_error: str | None
    warnings: list[str]


class RepoIngestJobOut(BaseModel):
    id: str
    status: str
    stage: str
    repo_url: str
    error: str | None
    analysis: RepoAnalysisOut | None


class RepoProjectCreate(BaseModel):
    """Confirm an analysis into a real project.

    Credentials arrive here rather than at analysis time: the repository
    describes the pipeline's structure, which is knowable without touching
    Snowflake, so making the user find credentials before they can see what the
    tool found is a needless gate.
    """

    name: str
    description: str | None = None
    connector_name: str = "snowflake"
    connector_type: str = "SNOWFLAKE"
    config: dict[str, Any] = {}

    # Which of the proposals to actually create. Absent means "all of them".
    databases: list[str] | None = None
    checks: list[str] | None = None


class GitHubRepositoryOut(BaseModel):
    full_name: str
    clone_url: str
    private: bool
    default_branch: str
    description: str | None
    pushed_at: str | None
    installation_id: int
    account: str


class GitHubStatusOut(BaseModel):
    """What the setup screen needs to decide which path to offer.

    `configured` is about the server (is a GitHub app set up at all), while
    `installations` is about this user (have they granted anything yet). The
    two failure modes need different wording, so they stay separate fields.
    """

    configured: bool
    install_url: str | None
    installations: list[dict[str, Any]]
    error: str | None = None


# --- Monitoring and maintenance ---------------------------------------


class IncidentEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    body: str
    author: str
    check_run_id: str | None
    created_at: datetime


class IncidentOut(BaseModel):
    id: str
    check_id: str
    check_name: str
    check_type: str
    database_name: str | None
    state: str
    severity: str
    title: str
    summary: str | None
    opened_at: datetime
    last_seen_at: datetime
    cleared_at: datetime | None
    failure_count: int
    escalation_count: int
    correlation_id: str | None
    triage_source: str
    # The Jira issue, as of the last sweep's refresh. Null means none exists -
    # Jira unconfigured, or filing failed.
    ticket_key: str | None
    ticket_url: str | None
    ticket_status: str | None
    ticket_assignee: str | None
    ticket_synced_at: datetime | None
    event_count: int

    @classmethod
    def from_model(cls, incident) -> "IncidentOut":
        return cls(
            id=incident.id,
            check_id=incident.check_id,
            check_name=incident.check.name,
            check_type=incident.check.type,
            database_name=incident.check.database.name if incident.check.database else None,
            state=incident.state,
            severity=incident.severity,
            title=incident.title,
            summary=incident.summary,
            opened_at=incident.opened_at,
            last_seen_at=incident.last_seen_at,
            cleared_at=incident.cleared_at,
            failure_count=incident.failure_count,
            escalation_count=incident.escalation_count,
            correlation_id=incident.correlation_id,
            triage_source=incident.triage_source,
            ticket_key=incident.ticket_key,
            ticket_url=incident.ticket_url,
            ticket_status=incident.ticket_status,
            ticket_assignee=incident.ticket_assignee,
            ticket_synced_at=incident.ticket_synced_at,
            event_count=len(incident.events),
        )


class IncidentDetailOut(IncidentOut):
    events: list[IncidentEventOut]

    @classmethod
    def from_model(cls, incident) -> "IncidentDetailOut":
        base = IncidentOut.from_model(incident)
        return cls(
            **base.model_dump(),
            events=[IncidentEventOut.model_validate(e) for e in incident.events],
        )


class RevisionOut(BaseModel):
    id: str
    check_id: str | None
    check_name: str | None
    database_name: str
    kind: str
    status: str
    current_config: dict[str, Any] | None
    proposed_name: str | None
    proposed_type: str | None
    proposed_schedule: str | None
    proposed_config: dict[str, Any] | None
    reason: str
    confidence: float | None
    triggered_by_commit: str | None
    created_at: datetime
    reviewed_at: datetime | None

    @classmethod
    def from_model(cls, revision) -> "RevisionOut":
        return cls(
            id=revision.id,
            check_id=revision.check_id,
            check_name=revision.check.name if revision.check else None,
            database_name=revision.database.name,
            kind=revision.kind,
            status=revision.status,
            current_config=revision.current_config,
            proposed_name=revision.proposed_name,
            proposed_type=revision.proposed_type,
            proposed_schedule=revision.proposed_schedule,
            proposed_config=revision.proposed_config,
            reason=revision.reason,
            confidence=revision.confidence,
            triggered_by_commit=revision.triggered_by_commit,
            created_at=revision.created_at,
            reviewed_at=revision.reviewed_at,
        )


class MonitorStatusOut(BaseModel):
    agent_enabled: bool
    # The model string, so a set-but-unusable model is distinguishable from
    # no model at all - they look identical from everywhere else.
    agent_model: str | None
    # Where issues are filed, or null when Jira is not configured and
    # incidents are tracked here only.
    ticket_backend: str | None
    monitor_interval_seconds: int
    maintenance_interval_seconds: int
    incident_counts: dict[str, int]
    pending_revisions: int


class SweepResultOut(BaseModel):
    synced: int
    reopened: int
    escalated: int
    agent_actions: int
    agent_error: str | None


class MaintenanceResultOut(BaseModel):
    project: str
    changed: bool = False
    revisions: int = 0
    auto_applied: int = 0
    undocumented_ddl: int = 0
    errors: list[str] = []
    agent_error: str | None = None
