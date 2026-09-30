import logging
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import models
from app.checks import repair
from app.checks.explain import PreviousRun, explain_run
from app.checks.defects import is_compile_error
from app.checks.engine import CheckOutcome, run_check
from app.config import settings
from app.models import cuid
from app.monitoring import triage
from app.rca.graph import generate_rca
from app.rca.object_repo_map import build_repo_context

logger = logging.getLogger(__name__)


def _run(check: models.Check) -> CheckOutcome:
    secondary = check.secondary_connector
    return run_check(
        check_type=check.type,
        config=check.config,
        connector_type=check.connector.type,
        connector_config=check.connector.config,
        secondary_connector_type=secondary.type if secondary else None,
        secondary_connector_config=secondary.config if secondary else None,
    )


def _invalid(summary: str, diagnostics: dict) -> CheckOutcome:
    return CheckOutcome(status=models.RunStatus.INVALID.value, metrics={"diagnostics": diagnostics}, message=summary)


def _resolve_error(db: Session, check: models.Check, outcome: CheckOutcome) -> CheckOutcome:
    """Decide what an ERROR run really was, before anyone is told about it.

    A pipeline error comes back unchanged. A defect in the check is repaired
    and re-run where a compiled repair exists - so the run records what the
    *fixed* check found, which may well be a real failure - and otherwise
    becomes INVALID. The assessment itself failing (the warehouse unreachable
    while describing tables) leaves the ERROR as it was: not knowing is not
    grounds to hide anything.
    """
    try:
        assessment = repair.assess_run(check, outcome.message)
    except Exception:  # noqa: BLE001 - fall back to reporting the error as-is
        logger.exception("could not assess errored run of check %s", check.id)
        return outcome

    if assessment.kind != "defect":
        return outcome

    if assessment.repaired_config is None or not repair.apply_repair(db, check, assessment):
        db.commit()  # a repair filed for review is still worth keeping
        return _invalid(assessment.summary, assessment.diagnostics)

    db.commit()
    rerun = _run(check)
    rerun.metrics = {**rerun.metrics, "repair": {"note": assessment.repair_note, **assessment.diagnostics}}
    if rerun.status == models.RunStatus.ERROR.value and is_compile_error(rerun.message):
        # Compiled under EXPLAIN and still rejected at run time. Stop here
        # rather than loop; the diagnostics carry both errors.
        return _invalid(
            "Not monitored: the check's SQL was invalid and could not be repaired automatically.",
            {**assessment.diagnostics, "rerun_error": (rerun.message or "")[:2000]},
        )
    return rerun


def _previous_run(db: Session, check: models.Check, run_id: str) -> PreviousRun | None:
    previous = (
        db.query(models.CheckRun)
        .filter(
            models.CheckRun.check_id == check.id,
            models.CheckRun.id != run_id,
            models.CheckRun.status != models.RunStatus.RUNNING.value,
        )
        .order_by(models.CheckRun.started_at.desc())
        .first()
    )
    if previous is None:
        return None
    return PreviousRun(
        id=previous.id, status=previous.status, metrics=previous.metrics, explanation=previous.explanation
    )


def _explain(db: Session, check: models.Check, run: models.CheckRun) -> None:
    """Attach the "what is off" box. Never allowed to cost the run itself.

    Runs after the run is committed, so the verdict is visible while a slow
    model is still writing. A failure here rolls back only the explanation;
    the run stays as committed and the page shows its message instead.
    """
    try:
        run.explanation = explain_run(
            check_name=check.name,
            check_type=check.type,
            description=check.description,
            config=check.config or {},
            status=run.status,
            metrics=run.metrics,
            message=run.message,
            previous=_previous_run(db, check, run.id),
            model_name=settings.agent_model.strip() or None,
            cooldown_minutes=settings.explain_ai_cooldown_minutes,
        )
        db.commit()
    except Exception:  # noqa: BLE001 - an explanation is an aid, never a gate
        db.rollback()
        logger.exception("could not explain run %s of check %s", run.id, check.id)


def execute_check(db: Session, check_id: str) -> str:
    check = db.query(models.Check).filter_by(id=check_id).one()
    connector = check.connector

    run = models.CheckRun(id=cuid(), check_id=check.id, status="RUNNING")
    db.add(run)
    db.commit()
    db.refresh(run)

    started = time.monotonic()
    outcome = _run(check)
    if outcome.status == models.RunStatus.ERROR.value:
        outcome = _resolve_error(db, check, outcome)
    duration_ms = int((time.monotonic() - started) * 1000)

    run.status = outcome.status
    run.metrics = outcome.metrics
    run.message = outcome.message
    run.finished_at = datetime.now(timezone.utc)
    run.duration_ms = duration_ms
    db.commit()
    _explain(db, check, run)

    if outcome.status == models.RunStatus.INVALID.value:
        # Nothing about the data was learned, so there is nothing to analyse
        # and nobody to page. Triage only stands down an incident this check
        # may have opened while it was misreporting its own fault as the
        # pipeline's.
        triage.on_invalid_run(db, check, run)
        return run.id

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
