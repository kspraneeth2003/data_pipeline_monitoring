import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import models
from app.checks.engine import run_check
from app.models import cuid
from app.monitoring import triage
from app.rca.graph import generate_rca
from app.rca.object_repo_map import build_repo_context


def execute_check(db: Session, check_id: str) -> str:
    check = db.query(models.Check).filter_by(id=check_id).one()
    connector = check.connector
    secondary = check.secondary_connector

    run = models.CheckRun(id=cuid(), check_id=check.id, status="RUNNING")
    db.add(run)
    db.commit()
    db.refresh(run)

    started = time.monotonic()
    outcome = run_check(
        check_type=check.type,
        config=check.config,
        connector_type=connector.type,
        connector_config=connector.config,
        secondary_connector_type=secondary.type if secondary else None,
        secondary_connector_config=secondary.config if secondary else None,
    )
    duration_ms = int((time.monotonic() - started) * 1000)

    run.status = outcome.status
    run.metrics = outcome.metrics
    run.message = outcome.message
    run.finished_at = datetime.now(timezone.utc)
    run.duration_ms = duration_ms
    db.commit()

    # Both branches hand off to triage, which owns the incident. Nothing here
    # decides whether to file a ticket any more: a run is an observation, and
    # one observation is not enough to know whether this is news. That
    # decision needs the incident's history, which is why it moved out.
    if outcome.status in ("FAILED", "ERROR"):
        rca = generate_rca(
            check_name=check.name,
            check_type=check.type,
            config=check.config,
            connector_type=connector.type,
            connector_config=connector.config,
            message=outcome.message,
            metrics=outcome.metrics,
            repo=build_repo_context(check),
        )
        db.add(
            models.RcaResult(
                id=cuid(),
                check_run_id=run.id,
                summary=rca["summary"],
                root_cause=rca["rootCause"],
                confidence=rca["confidence"],
                evidence=rca["evidence"],
                next_steps=rca["nextSteps"],
                suggested_owner=rca["suggestedOwner"],
            )
        )
        db.commit()
        triage.on_failed_run(db, check, run, rca)
    else:
        # A passing run is now meaningful: it is what closes an incident.
        # Previously it was discarded, which is why nothing ever got closed.
        triage.on_passed_run(db, check, run)

    return run.id
