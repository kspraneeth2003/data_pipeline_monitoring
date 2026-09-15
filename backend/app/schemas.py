from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ConnectorCreate(BaseModel):
    name: str
    type: str
    comment: str | None = None
    config: dict[str, Any] = {}
    test_connection: bool = True


class ConnectorUpdate(BaseModel):
    name: str | None = None
    comment: str | None = None
    config: dict[str, Any] | None = None
    test_connection: bool = True


class ConnectorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
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
