import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app import models, schemas
from app.db import get_db

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "project"


def _unique_slug(db: Session, base: str, exclude_id: str | None = None) -> str:
    slug, suffix = base, 2
    while True:
        query = db.query(models.Project).filter_by(slug=slug)
        if exclude_id:
            query = query.filter(models.Project.id != exclude_id)
        if not query.first():
            return slug
        slug, suffix = f"{base}-{suffix}", suffix + 1


def _resolve(db: Session, key: str) -> models.Project:
    """Projects are addressable by slug (what the UI uses in URLs) or by id."""
    project = db.query(models.Project).filter_by(slug=key).first()
    if not project:
        project = db.query(models.Project).filter_by(id=key).first()
    if not project:
        raise HTTPException(404, "Project not found")
    return project


def _health(db: Session, project: models.Project) -> schemas.ProjectHealth:
    counts = {"PASSED": 0, "FAILED": 0, "ERROR": 0}
    never_run = disabled = 0
    last_run_at = None

    for check in project.checks:
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

    open_tickets = (
        db.query(models.Ticket)
        .join(models.CheckRun, models.Ticket.check_run_id == models.CheckRun.id)
        .join(models.Check, models.CheckRun.check_id == models.Check.id)
        .filter(models.Check.project_id == project.id)
        .filter(models.Ticket.status != models.TicketStatus.DONE.value)
        .count()
    )

    # Worst state wins. A project with one failing check is a failing project -
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
        total_checks=len(project.checks),
        passing=counts["PASSED"],
        failing=counts["FAILED"],
        erroring=counts["ERROR"],
        never_run=never_run,
        disabled=disabled,
        open_tickets=open_tickets,
        last_run_at=last_run_at,
        status=status,
    )


def _with_health(db: Session, project: models.Project) -> schemas.ProjectWithHealthOut:
    return schemas.ProjectWithHealthOut(
        **schemas.ProjectOut.model_validate(project).model_dump(),
        health=_health(db, project),
    )


@router.get("", response_model=list[schemas.ProjectWithHealthOut])
def list_projects(db: Session = Depends(get_db)):
    projects = db.query(models.Project).order_by(models.Project.name).all()
    return [_with_health(db, p) for p in projects]


@router.post("", response_model=schemas.ProjectWithHealthOut, status_code=201)
def create_project(payload: schemas.ProjectCreate, db: Session = Depends(get_db)):
    slug = _unique_slug(db, _slugify(payload.slug or payload.name))
    project = models.Project(slug=slug, name=payload.name, description=payload.description)
    db.add(project)
    db.commit()
    db.refresh(project)
    return _with_health(db, project)


@router.get("/{key}", response_model=schemas.ProjectWithHealthOut)
def get_project(key: str, db: Session = Depends(get_db)):
    return _with_health(db, _resolve(db, key))


@router.patch("/{key}", response_model=schemas.ProjectWithHealthOut)
def update_project(key: str, payload: schemas.ProjectUpdate, db: Session = Depends(get_db)):
    project = _resolve(db, key)
    if payload.name is not None:
        project.name = payload.name
    if payload.description is not None:
        project.description = payload.description
    if payload.slug is not None:
        project.slug = _unique_slug(db, _slugify(payload.slug), exclude_id=project.id)
    db.commit()
    db.refresh(project)
    return _with_health(db, project)


@router.delete("/{key}", status_code=204)
def delete_project(key: str, db: Session = Depends(get_db)):
    project = _resolve(db, key)
    db.delete(project)
    db.commit()


@router.get("/{key}/checks", response_model=list[schemas.CheckOut])
def list_project_checks(key: str, db: Session = Depends(get_db)):
    project = _resolve(db, key)
    checks = (
        db.query(models.Check)
        .options(
            joinedload(models.Check.connector),
            joinedload(models.Check.secondary_connector),
            joinedload(models.Check.runs),
        )
        .filter_by(project_id=project.id)
        .order_by(models.Check.created_at)
        .all()
    )
    # The relationship has no ordering of its own, so the UI's runs[0] is only
    # "the latest run" if it is sorted here first. Same contract as /api/checks.
    for check in checks:
        check.runs = sorted(check.runs, key=lambda r: r.started_at, reverse=True)[:1]
    return checks


@router.get("/{key}/tickets", response_model=list[schemas.TicketWithContextOut])
def list_project_tickets(key: str, db: Session = Depends(get_db)):
    project = _resolve(db, key)
    rows = (
        db.query(models.Ticket, models.Check)
        .join(models.CheckRun, models.Ticket.check_run_id == models.CheckRun.id)
        .join(models.Check, models.CheckRun.check_id == models.Check.id)
        .filter(models.Check.project_id == project.id)
        .order_by(models.Ticket.created_at.desc())
        .all()
    )
    return [
        schemas.TicketWithContextOut(
            **schemas.TicketOut.model_validate(ticket).model_dump(),
            check_run_id=ticket.check_run_id,
            check_id=check.id,
            check_name=check.name,
        )
        for ticket, check in rows
    ]
