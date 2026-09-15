import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app import models, schemas
from app.connectors.security import encrypt_config_secrets, redact_config_secrets
from app.connectors.snowflake_connector import test_snowflake_connection
from app.db import get_db

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "item"


def _unique_project_slug(db: Session, base: str, exclude_id: str | None = None) -> str:
    slug, suffix = base, 2
    while True:
        query = db.query(models.Project).filter_by(slug=slug)
        if exclude_id:
            query = query.filter(models.Project.id != exclude_id)
        if not query.first():
            return slug
        slug, suffix = f"{base}-{suffix}", suffix + 1


def _unique_database_slug(db: Session, project_id: str, base: str, exclude_id: str | None = None) -> str:
    slug, suffix = base, 2
    while True:
        query = db.query(models.Database).filter_by(project_id=project_id, slug=slug)
        if exclude_id:
            query = query.filter(models.Database.id != exclude_id)
        if not query.first():
            return slug
        slug, suffix = f"{base}-{suffix}", suffix + 1


def _resolve_project(db: Session, key: str) -> models.Project:
    """Addressable by slug (what the UI puts in URLs) or by id."""
    project = db.query(models.Project).filter_by(slug=key).first()
    if not project:
        project = db.query(models.Project).filter_by(id=key).first()
    if not project:
        raise HTTPException(404, "Project not found")
    return project


def _resolve_database(db: Session, project: models.Project, key: str) -> models.Database:
    database = db.query(models.Database).filter_by(project_id=project.id, slug=key).first()
    if not database:
        database = db.query(models.Database).filter_by(project_id=project.id, id=key).first()
    if not database:
        raise HTTPException(404, "Database not found in this project")
    return database


def _health_of_checks(db: Session, checks: list[models.Check]) -> schemas.ProjectHealth:
    counts = {"PASSED": 0, "FAILED": 0, "ERROR": 0}
    never_run = disabled = 0
    last_run_at = None

    for check in checks:
        if not check.enabled:
            disabled += 1
        latest = max(check.runs, key=lambda r: r.started_at, default=None)
        if latest is None:
            never_run += 1
            continue
        if last_run_at is None or latest.started_at > last_run_at:
            last_run_at = latest.started_at
        if latest.status in counts:
            counts[latest.status] += 1

    open_tickets = 0
    if checks:
        open_tickets = (
            db.query(models.Ticket)
            .join(models.CheckRun, models.Ticket.check_run_id == models.CheckRun.id)
            .filter(models.CheckRun.check_id.in_([c.id for c in checks]))
            .filter(models.Ticket.status != models.TicketStatus.DONE.value)
            .count()
        )

    # Worst state wins. A database with one failing check is a failing database -
    # averaging it into "mostly green" is how real breakage gets ignored.
    if counts["ERROR"]:
        status = "ERROR"
    elif counts["FAILED"]:
        status = "FAILED"
    elif counts["PASSED"]:
        status = "PASSED"
    else:
        status = "NONE"

    return schemas.ProjectHealth(
        total_checks=len(checks),
        passing=counts["PASSED"],
        failing=counts["FAILED"],
        erroring=counts["ERROR"],
        never_run=never_run,
        disabled=disabled,
        open_tickets=open_tickets,
        last_run_at=last_run_at,
        status=status,
    )


def _database_out(db: Session, database: models.Database) -> schemas.DatabaseWithHealthOut:
    return schemas.DatabaseWithHealthOut(
        **schemas.DatabaseOut.model_validate(database).model_dump(),
        connector=schemas.ConnectorRef.model_validate(database.connector),
        health=_health_of_checks(db, database.checks),
    )


def _project_out(db: Session, project: models.Project) -> schemas.ProjectWithHealthOut:
    return schemas.ProjectWithHealthOut(
        **schemas.ProjectOut.model_validate(project).model_dump(),
        health=_health_of_checks(db, project.checks),
        databases=[_database_out(db, d) for d in project.databases],
    )


def _sorted_latest_run(checks: list[models.Check], limit: int = 1) -> None:
    """The runs relationship has no ordering, so the UI's runs[0] is only the
    latest run if it is sorted here first."""
    for check in checks:
        check.runs = sorted(check.runs, key=lambda r: r.started_at, reverse=True)[:limit]


# --- Projects ------------------------------------------------------------


@router.get("", response_model=list[schemas.ProjectWithHealthOut])
def list_projects(db: Session = Depends(get_db)):
    projects = db.query(models.Project).order_by(models.Project.name).all()
    return [_project_out(db, p) for p in projects]


@router.post("", response_model=schemas.ProjectWithHealthOut, status_code=201)
def create_project(payload: schemas.ProjectCreate, db: Session = Depends(get_db)):
    project = models.Project(
        slug=_unique_project_slug(db, _slugify(payload.slug or payload.name)),
        name=payload.name,
        description=payload.description,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return _project_out(db, project)


@router.post("/setup", response_model=schemas.ProjectWithHealthOut, status_code=201)
def setup_project(payload: schemas.ProjectSetup, db: Session = Depends(get_db)):
    """Name it, connect it, pick its databases - in one call.

    The connection is tested before anything is written, so a project is never
    created in a half-configured state that the user then has to clean up.
    """
    if payload.connector_type == "SNOWFLAKE":
        if not payload.config.get("password") and not payload.config.get("privateKey"):
            raise HTTPException(400, "Provide either a password or a private key")
        try:
            test_snowflake_connection(payload.config)
        except Exception as error:  # noqa: BLE001
            raise HTTPException(400, f"Connection test failed: {error}") from error

    project = models.Project(
        slug=_unique_project_slug(db, _slugify(payload.name)),
        name=payload.name,
        description=payload.description,
    )
    db.add(project)
    db.flush()

    connector = models.Connector(
        project_id=project.id,
        name=payload.connector_name or "snowflake",
        type=payload.connector_type,
        config=encrypt_config_secrets(payload.connector_type, payload.config),
    )
    db.add(connector)
    db.flush()

    for name in dict.fromkeys(n.strip() for n in payload.databases if n.strip()):
        db.add(
            models.Database(
                project_id=project.id,
                connector_id=connector.id,
                name=name,
                slug=_unique_database_slug(db, project.id, _slugify(name)),
            )
        )

    db.commit()
    db.refresh(project)
    return _project_out(db, project)


@router.get("/{key}", response_model=schemas.ProjectWithHealthOut)
def get_project(key: str, db: Session = Depends(get_db)):
    return _project_out(db, _resolve_project(db, key))


@router.patch("/{key}", response_model=schemas.ProjectWithHealthOut)
def update_project(key: str, payload: schemas.ProjectUpdate, db: Session = Depends(get_db)):
    project = _resolve_project(db, key)
    if payload.name is not None:
        project.name = payload.name
    if payload.description is not None:
        project.description = payload.description
    if payload.slug is not None:
        project.slug = _unique_project_slug(db, _slugify(payload.slug), exclude_id=project.id)
    db.commit()
    db.refresh(project)
    return _project_out(db, project)


@router.delete("/{key}", status_code=204)
def delete_project(key: str, db: Session = Depends(get_db)):
    db.delete(_resolve_project(db, key))
    db.commit()


@router.get("/{key}/tickets", response_model=list[schemas.TicketWithContextOut])
def list_project_tickets(key: str, db: Session = Depends(get_db)):
    project = _resolve_project(db, key)
    rows = (
        db.query(models.Ticket, models.Check, models.Database)
        .join(models.CheckRun, models.Ticket.check_run_id == models.CheckRun.id)
        .join(models.Check, models.CheckRun.check_id == models.Check.id)
        .join(models.Database, models.Check.database_id == models.Database.id)
        .filter(models.Database.project_id == project.id)
        .order_by(models.Ticket.created_at.desc())
        .all()
    )
    return [
        schemas.TicketWithContextOut(
            **schemas.TicketOut.model_validate(ticket).model_dump(),
            check_run_id=ticket.check_run_id,
            check_id=check.id,
            check_name=check.name,
            database_slug=database.slug,
            database_name=database.name,
            project_slug=project.slug,
        )
        for ticket, check, database in rows
    ]


# --- Databases within a project -----------------------------------------


@router.get("/{key}/connectors", response_model=list[schemas.ConnectorOut])
def list_project_connectors(key: str, db: Session = Depends(get_db)):
    project = _resolve_project(db, key)
    return [
        schemas.ConnectorOut.model_validate(
            {
                **schemas.ConnectorOut.model_validate(c).model_dump(),
                "config": redact_config_secrets(c.type, c.config),
                "checks_count": sum(len(d.checks) for d in project.databases if d.connector_id == c.id),
            }
        )
        for c in project.connectors
    ]


@router.get("/{key}/databases", response_model=list[schemas.DatabaseWithHealthOut])
def list_databases(key: str, db: Session = Depends(get_db)):
    project = _resolve_project(db, key)
    return [_database_out(db, d) for d in project.databases]


@router.post("/{key}/databases", response_model=schemas.DatabaseWithHealthOut, status_code=201)
def create_database(key: str, payload: schemas.DatabaseCreate, db: Session = Depends(get_db)):
    project = _resolve_project(db, key)
    if not db.query(models.Connector).filter_by(id=payload.connector_id).first():
        raise HTTPException(400, "connector_id does not match an existing connector")

    name = payload.name.strip()
    if db.query(models.Database).filter_by(project_id=project.id, name=name).first():
        raise HTTPException(400, f"{name} is already in this project")

    database = models.Database(
        project_id=project.id,
        connector_id=payload.connector_id,
        name=name,
        slug=_unique_database_slug(db, project.id, _slugify(payload.slug or name)),
        description=payload.description,
    )
    db.add(database)
    db.commit()
    db.refresh(database)
    return _database_out(db, database)


@router.get("/{key}/databases/{db_key}", response_model=schemas.DatabaseWithHealthOut)
def get_database(key: str, db_key: str, db: Session = Depends(get_db)):
    project = _resolve_project(db, key)
    return _database_out(db, _resolve_database(db, project, db_key))


@router.patch("/{key}/databases/{db_key}", response_model=schemas.DatabaseWithHealthOut)
def update_database(key: str, db_key: str, payload: schemas.DatabaseUpdate, db: Session = Depends(get_db)):
    project = _resolve_project(db, key)
    database = _resolve_database(db, project, db_key)

    if payload.name is not None:
        database.name = payload.name.strip()
    if payload.description is not None:
        database.description = payload.description
    if payload.connector_id is not None:
        if not db.query(models.Connector).filter_by(id=payload.connector_id).first():
            raise HTTPException(400, "connector_id does not match an existing connector")
        database.connector_id = payload.connector_id
    if payload.slug is not None:
        database.slug = _unique_database_slug(
            db, project.id, _slugify(payload.slug), exclude_id=database.id
        )

    db.commit()
    db.refresh(database)
    return _database_out(db, database)


@router.delete("/{key}/databases/{db_key}", status_code=204)
def delete_database(key: str, db_key: str, db: Session = Depends(get_db)):
    project = _resolve_project(db, key)
    db.delete(_resolve_database(db, project, db_key))
    db.commit()


@router.get("/{key}/databases/{db_key}/checks", response_model=list[schemas.CheckOut])
def list_database_checks(key: str, db_key: str, db: Session = Depends(get_db)):
    project = _resolve_project(db, key)
    database = _resolve_database(db, project, db_key)
    checks = (
        db.query(models.Check)
        .options(
            joinedload(models.Check.connector),
            joinedload(models.Check.secondary_connector),
            joinedload(models.Check.database).joinedload(models.Database.project),
            joinedload(models.Check.runs),
        )
        .filter_by(database_id=database.id)
        .order_by(models.Check.created_at)
        .all()
    )
    _sorted_latest_run(checks)
    return checks
