"""The monitor's heartbeat: the work that needs a view wider than one run.

Incident bookkeeping happens the moment a check runs (`checks/runner.py` ->
`triage`), because a state transition should not wait for a timer. What lands
here is everything that cannot be decided from a single run:

* **Jira sync.** What the issue's status and assignee are now, which only
  Jira knows.
* **Escalation.** "Nobody has responded" is a statement about elapsed time and
  an unchanged issue, which no individual run can observe.
* **Reopening.** An issue closed while its check still fails is only visible
  by comparing the runs against the clear.
* **Agent judgement.** Correlation, warning severity, and written commentary
  need the incident's whole history and its siblings.

The order is deliberate: the deterministic passes run first and always, then
the agent is offered what remains. A sweep with no agent configured is a
complete sweep, not a degraded one.
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import Incident, IncidentState
from app.monitoring import triage

logger = logging.getLogger("dpm.monitoring.sweep")


def active_incidents(db: Session) -> list[Incident]:
    return list(
        db.scalars(
            select(Incident)
            .where(Incident.state.in_([IncidentState.OPEN.value, IncidentState.WARNING.value]))
            .order_by(Incident.opened_at)
        ).all()
    )


def run_sweep(db: Session) -> dict:
    """One pass. Returns what it did, so the scheduler can log it and the
    API can show the monitor is alive rather than merely configured."""
    result = {
        "synced": 0,
        "reopened": 0,
        "escalated": 0,
        "agent_actions": 0,
        "agent_error": None,
    }

    # Jira first, because both passes below ask whether anyone has responded
    # and that is a fact about Jira rather than about this database. One
    # refresh per sweep keeps the network call out of every decision.
    try:
        result["synced"] = triage.sync_ticket_state(db, active_incidents(db))
    except Exception:
        logger.exception("Jira sync failed; escalation will use the cached state")
        db.rollback()

    # Deterministic passes next. Each commits independently: a failure in
    # one must not roll back the work of another, since they are unrelated
    # decisions that happen to share a timer.
    try:
        result["reopened"] = len(triage.reopen_closed_but_failing(db))
    except Exception:
        logger.exception("Reopen pass failed")
        db.rollback()

    try:
        result["escalated"] = len(triage.escalate_stale(db))
    except Exception:
        logger.exception("Escalation pass failed")
        db.rollback()

    if not settings.agent_enabled:
        return result

    # The agent is strictly additive. Anything it raises costs its own
    # contribution and nothing else - the incidents above are already
    # recorded and committed.
    try:
        from app.monitoring.agent import review_incidents

        result["agent_actions"] = review_incidents(db, active_incidents(db))
    except Exception as error:  # noqa: BLE001
        logger.exception("Reporting agent failed")
        db.rollback()
        result["agent_error"] = str(error)[:300]

    return result


def sweep_job() -> None:
    """Scheduler entry point. Owns its session, and never raises - an
    exception escaping here would kill the APScheduler job and silently stop
    monitoring until the process restarts."""
    db = SessionLocal()
    try:
        result = run_sweep(db)
        if any(v for k, v in result.items() if k != "agent_error"):
            logger.info("Monitor sweep: %s", result)
    except Exception:
        logger.exception("Monitor sweep failed")
    finally:
        db.close()
