"""Rule-based triage: what the monitor does with no model available.

This is the floor, in the same sense `rca/heuristic.py` is the floor for RCA.
It is deliberately dumb - it opens on failure, comments on repeat, clears on
recovery, escalates on age - and it is deliberately complete: with the agent
switched off, DPM still files one ticket per incident instead of one per run,
still comments as things develop, and still closes what recovered. The agent
replaces the *judgement*, not the plumbing.

What the rules cannot do, and the agent can:

* Decide that eight checks failing at once are one upstream event.
* Read a metrics trend and call something a warning before it fails.
* Tell a flapping check from a real recurrence.
* Write a comment that says what changed rather than that something changed.

Keeping those out of here is what keeps this file honest. Every rule below is
a threshold someone can read off the page and predict.
"""

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.models import (
    Incident,
    IncidentEventKind,
    IncidentState,
    RunStatus,
    TicketPriority,
    TicketStatus,
)
from app.monitoring import incidents as lifecycle
from app.tickets.base import TicketContent
from app.tickets.registry import ticket_backend

logger = logging.getLogger("dpm.monitoring.triage")

# A cleared incident that fails again within this window is the same incident
# coming back, not a new one. Beyond it, whatever went wrong went wrong again
# for reasons worth tracking separately.
REOPEN_WINDOW = timedelta(hours=6)


def severity_for(run_status: str, failure_count: int) -> str:
    """An ERROR outrules a FAILED: a check that could not run at all means the
    warehouse or the credentials are wrong, which blocks every other answer.
    Sustained failure escalates on its own - something broken for ten runs is
    not the same news as something broken once."""
    if run_status == RunStatus.ERROR.value:
        return TicketPriority.HIGH.value
    if failure_count >= 10:
        return TicketPriority.HIGH.value
    return TicketPriority.MEDIUM.value


def describe_failure(check: models.Check, run: models.CheckRun) -> str:
    verb = "errored" if run.status == RunStatus.ERROR.value else "failed"
    return f'Check "{check.name}" ({check.type}) {verb}: {run.message or "no message"}'


def build_ticket_content(
    check: models.Check, incident: Incident, run: models.CheckRun, rca: dict | None
) -> TicketContent:
    lines = [describe_failure(check, run), ""]
    if rca:
        source = "LLM" if (rca.get("evidence") or {}).get("source") == "llm" else "heuristic"
        confidence = round((rca.get("confidence") or 0) * 100)
        lines += [
            f"Root cause ({source} analysis, {confidence}% confidence):",
            rca.get("rootCause") or "Not determined.",
            "",
            f"Next steps: {rca.get('nextSteps') or 'Not determined.'}",
            "",
        ]
    lines.append(f"Incident {incident.id} - further updates will be added as comments.")

    return TicketContent(
        title=f"{check.name}: {'error' if run.status == RunStatus.ERROR.value else 'failure'} detected",
        description="\n".join(lines),
        priority=incident.severity,
        assignee=(rca or {}).get("suggestedOwner"),
        labels=("dpm", f"check-{check.type.lower().replace('_', '-')}"),
    )


def recently_cleared_incident(db: Session, check_id: str) -> Incident | None:
    cutoff = lifecycle.utcnow() - REOPEN_WINDOW
    return db.scalars(
        select(Incident)
        .where(
            Incident.check_id == check_id,
            Incident.state == IncidentState.CLEARED.value,
            Incident.cleared_at.is_not(None),
            Incident.cleared_at >= cutoff,
        )
        .order_by(Incident.cleared_at.desc())
    ).first()


def on_failed_run(db: Session, check: models.Check, run: models.CheckRun, rca: dict | None) -> Incident:
    """A check just failed. Open, continue, or revive an incident for it."""
    incident = lifecycle.active_incident_for(db, check.id)

    if incident is None:
        revived = recently_cleared_incident(db, check.id)
        if revived is not None:
            lifecycle.reopen_incident(
                db,
                revived,
                f"Failing again {_ago(revived)} after clearing - treating this as the same "
                f"incident recurring rather than a new one. {run.message or ''}".strip(),
                check_run_id=run.id,
            )
            revived.failure_count += 1
            revived.severity = severity_for(run.status, revived.failure_count)
            db.commit()
            return revived

        incident = lifecycle.open_incident(
            db,
            check,
            run,
            title=f"{check.name}: {'error' if run.status == RunStatus.ERROR.value else 'failure'}",
            summary=(rca or {}).get("summary") or describe_failure(check, run),
            severity=severity_for(run.status, 1),
        )
        content = build_ticket_content(check, incident, run, rca)
        ref = ticket_backend().create(db, incident, content)
        db.add(
            models.Ticket(
                id=models.cuid(),
                incident_id=incident.id,
                check_run_id=run.id,
                key=ref.key,
                title=content.title,
                description=content.description,
                priority=content.priority,
                assignee=content.assignee,
                status=TicketStatus.TODO.value,
                external_key=ref.external_key,
                external_url=ref.external_url,
            )
        )
        db.commit()
        return incident

    # Already open. Record it, and comment only when there is news - a
    # comment on every run of a check failing all day is how a ticket becomes
    # unreadable and then muted.
    previous_message = _last_reported_message(db, incident)
    lifecycle.record_failure(db, incident, run)
    incident.severity = severity_for(run.status, incident.failure_count)

    if run.message and run.message != previous_message:
        body = (
            f"Still failing ({incident.failure_count} runs). The message changed:\n"
            f"  was: {previous_message or 'not recorded'}\n"
            f"  now: {run.message}"
        )
        lifecycle.add_event(db, incident, IncidentEventKind.COMMENT, body, check_run_id=run.id)
        if incident.ticket:
            ticket_backend().comment(db, incident.ticket, body)
    db.commit()
    return incident


def on_passed_run(db: Session, check: models.Check, run: models.CheckRun) -> Incident | None:
    """A check just passed. Clear its incident once recovery is convincing."""
    incident = lifecycle.active_incident_for(db, check.id)
    if incident is None:
        return None

    lifecycle.record_pass(db, incident, run)
    if incident.consecutive_passes < lifecycle.CLEAR_AFTER_PASSES:
        db.commit()
        return incident

    body = (
        f"Recovered - {incident.consecutive_passes} consecutive passing runs after "
        f"{incident.failure_count} failure(s). Open for {_duration(incident)}."
    )
    lifecycle.clear_incident(db, incident, body, check_run_id=run.id)
    if incident.ticket:
        ticket_backend().transition(db, incident.ticket, TicketStatus.DONE.value, body)
    db.commit()
    return incident


def escalate_stale(db: Session) -> list[Incident]:
    """Poke the incidents nobody has responded to.

    This is the "not even being noticed" case, and it is about the human side
    rather than the pipeline: the check is still failing, the ticket is still
    untouched, and silence has been read as resolution. Backed off so a
    long-running incident produces occasional escalations rather than a daily
    nag, which gets filtered and then ignored.
    """
    now = lifecycle.utcnow()
    stale_before = now - timedelta(hours=settings.escalate_after_hours)
    backoff_before = now - timedelta(hours=settings.escalation_backoff_hours)

    candidates = db.scalars(
        select(Incident).where(
            Incident.state == IncidentState.OPEN.value,
            Incident.opened_at <= stale_before,
        )
    ).all()

    escalated: list[Incident] = []
    for incident in candidates:
        if incident.last_escalated_at and incident.last_escalated_at > backoff_before:
            continue
        ticket = incident.ticket
        # A ticket someone has picked up is being noticed. Only untouched
        # ones are escalated - the point is absence of response, not slowness.
        if ticket and ticket.status != TicketStatus.TODO.value:
            continue

        incident.escalation_count += 1
        incident.last_escalated_at = now
        if incident.severity != TicketPriority.CRITICAL.value:
            incident.severity = (
                TicketPriority.CRITICAL.value
                if incident.escalation_count >= 2
                else TicketPriority.HIGH.value
            )
        body = (
            f"No response after {_duration(incident)} and {incident.failure_count} failed "
            f"run(s); the ticket is still {ticket.status if ticket else 'unfiled'}. "
            f"Raising priority to {incident.severity}."
        )
        lifecycle.add_event(db, incident, IncidentEventKind.ESCALATED, body)
        if ticket:
            ticket.priority = incident.severity
            ticket_backend().comment(db, ticket, body)
        escalated.append(incident)

    if escalated:
        db.commit()
    return escalated


def reopen_closed_but_failing(db: Session) -> list[Incident]:
    """Closed is not the same as fixed.

    A ticket marked DONE while its check is still failing is the most
    dangerous state the system can be in: everyone believes it is handled and
    nothing is watching. Reopening is the one place the monitor overrides a
    human decision, and it says so in the comment rather than doing it
    quietly.
    """
    reopened: list[Incident] = []
    candidates = db.scalars(
        select(Incident)
        .join(models.Ticket, models.Ticket.incident_id == Incident.id)
        .where(
            Incident.state == IncidentState.CLEARED.value,
            models.Ticket.status == TicketStatus.DONE.value,
        )
    ).all()

    for incident in candidates:
        # Only runs at or after the clear can say anything about whether the
        # closure was premature; earlier ones are the failures it was closed
        # for. `id` breaks ties on `started_at` so the answer is stable -
        # this query decides whether to override a person's decision to close
        # a ticket, and that must not come down to row order.
        query = select(models.CheckRun).where(models.CheckRun.check_id == incident.check_id)
        if incident.cleared_at is not None:
            query = query.where(models.CheckRun.started_at >= incident.cleared_at)
        latest = db.scalars(
            query.order_by(models.CheckRun.started_at.desc(), models.CheckRun.id.desc())
        ).first()

        if latest is None or latest.status in (
            RunStatus.PASSED.value,
            RunStatus.RUNNING.value,
        ):
            continue

        body = (
            f"Reopening: the ticket is closed but {incident.check.name} is still failing as "
            f"of the run at {latest.started_at:%Y-%m-%d %H:%M} UTC ({latest.message or 'no message'})."
        )
        lifecycle.reopen_incident(db, incident, body, check_run_id=latest.id)
        if incident.ticket:
            incident.ticket.status = TicketStatus.TODO.value
            ticket_backend().comment(db, incident.ticket, body)
        reopened.append(incident)

    if reopened:
        db.commit()
    return reopened


def _last_reported_message(db: Session, incident: Incident) -> str | None:
    run = db.get(models.CheckRun, incident.last_run_id) if incident.last_run_id else None
    return run.message if run else None


def _duration(incident: Incident) -> str:
    delta = lifecycle.utcnow() - incident.opened_at
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"{int(delta.total_seconds() // 60)} minutes"
    if hours < 48:
        return f"{hours:.1f} hours"
    return f"{hours / 24:.1f} days"


def _ago(incident: Incident) -> str:
    if not incident.cleared_at:
        return "recently"
    delta = lifecycle.utcnow() - incident.cleared_at
    minutes = delta.total_seconds() / 60
    return f"{int(minutes)} minutes" if minutes < 90 else f"{minutes / 60:.1f} hours"
