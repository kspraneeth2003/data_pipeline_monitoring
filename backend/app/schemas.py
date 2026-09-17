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
    type: str | None = None
    schedule: str | None = None
    enabled: bool | None = None
    database_id: str | None = None
    connector_id: str | None = None
    secondary_connector_id: str | None = None
    config: dict[str, Any] | None = None


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


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    key: str
    title: str
    description: str
    priority: str
    assignee: str | None
    status: str
    created_at: datetime
    updated_at: datetime


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
    ticket: TicketOut | None = None


class CheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    type: str
    schedule: str
    enabled: bool
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


class TicketWithContextOut(TicketOut):
    check_run_id: str
    check_id: str
    check_name: str
    # Enough context to link straight to the check in the project tree.
    database_slug: str
    database_name: str
    project_slug: str


class TicketUpdate(BaseModel):
    status: str | None = None
    assignee: str | None = None


class ProjectHealth(BaseModel):
    """Rollup shown on a project card. Ordered so the worst state wins: a
    project with one failing check reads as failing, not as "mostly passing"."""

    total_checks: int
    passing: int
    failing: int
    erroring: int
    never_run: int
    disabled: int
    open_tickets: int
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
    """Start an analysis. The token is used for the git fetch and then dropped -
    it is never stored, because nothing the app does later needs to read the
    repository again with the user's credentials."""

    repo_url: str
    token: str | None = None
    ref: str | None = None


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


class ProposedDatabase(BaseModel):
    name: str
    description: str
    repo_paths: dict[str, str]
    tables: list[str]


class RepoAnalysisOut(BaseModel):
    repo_url: str
    repo_ref: str | None
    repo_commit: str | None
    repo_commit_subject: str | None
    project_name: str
    project_description: str
    databases: list[ProposedDatabase]
    checks: list[ProposedCheck]
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
