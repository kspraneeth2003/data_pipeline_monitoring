import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import models
from app.checks.engine import run_check
from app.models import cuid
from app.rca.graph import generate_rca
from app.tickets.mock_ticket import create_mock_ticket


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

    if outcome.status in ("FAILED", "ERROR"):
        rca = generate_rca(
            check_name=check.name,
            check_type=check.type,
            config=check.config,
            connector_type=connector.type,
            connector_config=connector.config,
            message=outcome.message,
            metrics=outcome.metrics,
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

        create_mock_ticket(
            db,
            check_run_id=run.id,
            check_name=check.name,
            check_type=check.type,
            run_status=outcome.status,
            message=outcome.message,
            rca=rca,
        )

    return run.id
