"""Incidents, revisions, and manual triggers for both agents.

The triggers exist because both agents are otherwise invisible until their
timer fires. "Run it now and show me what it decided" is how you tell a
misconfigured model from a quiet one, and waiting fifteen minutes to find
out is how people conclude the feature is broken.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models, schemas
from app.config import settings
from app.db import get_db
from app.maintenance.service import run_maintenance
from app.models import (
    CheckRevision,
    Incident,
    IncidentState,
    RevisionStatus,
)
from app.monitoring.incidents import utcnow
from app.monitoring.sweep import run_sweep

router = APIRouter(prefix="/api", tags=["monitoring"])


def _project_or_404(db: Session, slug: str) -> models.Project:
    project = db.scalars(select(models.Project).where(models.Project.slug == slug)).first()
    if project is None:
        raise HTTPException(404, "Project not found")
    return project


@router.get("/monitor/status", response_model=schemas.MonitorStatusOut)
def monitor_status(db: Session = Depends(get_db)):
    """Whether the agents are configured, and what they are looking at.

    Reports the model name rather than a bare "enabled", because the most
    common failure is a model string that is set but unusable - and that
    looks identical to "on" from anywhere else.
    """
    counts = {state.value: 0 for state in IncidentState}
    for state, count in db.execute(
        select(Incident.state, models.func.count(Incident.id)).group_by(Incident.state)
    ):
        counts[state] = count

    return schemas.MonitorStatusOut(
        agent_enabled=settings.agent_enabled,
        agent_model=settings.agent_model or None,
        ticket_backend="jira" if settings.jira_configured else "board",
        monitor_interval_seconds=settings.monitor_interval_seconds,
        maintenance_interval_seconds=settings.maintenance_interval_seconds,
        incident_counts=counts,
        pending_revisions=db.scalar(
            select(models.func.count(CheckRevision.id)).where(
                CheckRevision.status == RevisionStatus.PENDING.value
            )
        )
        or 0,
    )


@router.get("/projects/{slug}/incidents", response_model=list[schemas.IncidentOut])
def list_incidents(slug: str, state: str | None = None, db: Session = Depends(get_db)):
    project = _project_or_404(db, slug)
    query = (
        select(Incident)
        .join(models.Check, models.Check.id == Incident.check_id)
        .join(models.Database, models.Database.id == models.Check.database_id)
        .where(models.Database.project_id == project.id)
        .order_by(Incident.opened_at.desc())
    )
    if state:
        query = query.where(Incident.state == state.upper())
    return [schemas.IncidentOut.from_model(i) for i in db.scalars(query).all()]


@router.get("/incidents/{incident_id}", response_model=schemas.IncidentDetailOut)
def get_incident(incident_id: str, db: Session = Depends(get_db)):
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(404, "Incident not found")
    return schemas.IncidentDetailOut.from_model(incident)


@router.post("/monitor/sweep", response_model=schemas.SweepResultOut)
def trigger_sweep(db: Session = Depends(get_db)):
    """Run the monitor now instead of waiting for the timer."""
    return schemas.SweepResultOut(**run_sweep(db))


@router.get("/projects/{slug}/revisions", response_model=list[schemas.RevisionOut])
def list_revisions(slug: str, status: str | None = None, db: Session = Depends(get_db)):
    project = _project_or_404(db, slug)
    query = (
        select(CheckRevision)
        .join(models.Database, models.Database.id == CheckRevision.database_id)
        .where(models.Database.project_id == project.id)
        .order_by(CheckRevision.created_at.desc())
    )
    if status:
        query = query.where(CheckRevision.status == status.upper())
    return [schemas.RevisionOut.from_model(r) for r in db.scalars(query).all()]


@router.post("/projects/{slug}/maintenance", response_model=schemas.MaintenanceResultOut)
def trigger_maintenance(slug: str, db: Session = Depends(get_db)):
    """Check this project's DDL for changes now, and reconcile."""
    project = _project_or_404(db, slug)
    if not project.repo_url:
        raise HTTPException(
            400, "This project has no repository, so there is no DDL to compare against"
        )
    return schemas.MaintenanceResultOut(
        **run_maintenance(db, project, settings.maintenance_scan_warehouse)
    )


@router.post("/revisions/{revision_id}/apply", response_model=schemas.RevisionOut)
def apply_revision_endpoint(revision_id: str, db: Session = Depends(get_db)):
    """Accept a proposed change.

    Goes through the same `apply_revision` the agent's auto-apply path uses,
    so a reviewed change and an automatic one are applied by identical code
    rather than by a second, less-exercised branch.
    """
    from app.maintenance.agent import apply_revision

    revision = db.get(CheckRevision, revision_id)
    if revision is None:
        raise HTTPException(404, "Revision not found")
    if revision.status != RevisionStatus.PENDING.value:
        raise HTTPException(409, f"This revision is already {revision.status.lower()}")

    apply_revision(db, revision, auto=False)
    db.commit()
    db.refresh(revision)
    return schemas.RevisionOut.from_model(revision)


@router.post("/revisions/{revision_id}/reject", response_model=schemas.RevisionOut)
def reject_revision(revision_id: str, db: Session = Depends(get_db)):
    """Decline a proposed change.

    The row is kept rather than deleted: it is the record that somebody
    considered this and said no, which is what stops the agent proposing it
    again on the next detected change.
    """
    revision = db.get(CheckRevision, revision_id)
    if revision is None:
        raise HTTPException(404, "Revision not found")
    if revision.status != RevisionStatus.PENDING.value:
        raise HTTPException(409, f"This revision is already {revision.status.lower()}")

    revision.status = RevisionStatus.REJECTED.value
    revision.reviewed_at = utcnow()
    db.commit()
    db.refresh(revision)
    return schemas.RevisionOut.from_model(revision)
