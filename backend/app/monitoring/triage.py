"""Rule-based triage: what the monitor does with no model available.

This is the floor, in the same sense `rca/heuristic.py` is the floor for RCA.
It is deliberately dumb - it opens on failure, comments on repeat, clears on
recovery, escalates on age - and it is deliberately complete: with the agent
switched off, DPM still files one Jira issue per incident instead of one per
run, still comments as things develop, and still closes what recovered. The
agent replaces the *judgement*, not the plumbing.

What the rules cannot do, and the agent can:

* Decide that eight checks failing at once are one upstream event.
* Read a metrics trend and call something a warning before it fails.
* Tell a flapping check from a real recurrence.
* Write a comment that says what changed rather than that something changed.

Keeping those out of here is what keeps this file honest. Every rule below is
a threshold someone can read off the page and predict.

**Incidents live here; issues live in Jira.** Everything in this module
works whether or not a tracker is configured - the incident and its event
stream are written to Postgres either way, and the Jira call is an extra
that may fail. An unreachable tracker costs the external copy of a comment,
never the record of what happened.
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
    IncidentSeverity,
    IncidentState,
    RunStatus,
)
from app.monitoring import incidents as lifecycle
from app.tickets.base import TicketContent
from app.tickets.registry import ticket_backend

logger = logging.getLogger("dpm.monitoring.triage")

# A cleared incident that fails again within this window is the same incident
# coming back, not a new one. Beyond it, whatever went wrong went wrong again
# for reasons worth tracking separately.
REOPEN_WINDOW = timedelta(hours=6)

# Jira status names that mean "nobody has picked this up". Compared
# lower-cased and loosely, because workflows are per-project: "To Do",
# "Open", "Backlog" and "Selected for Development" all mean untouched, and a
# site that renamed one is normal rather than misconfigured.
UNTOUCHED_STATUSES = {"to do", "todo", "open", "backlog", "new", "selected for development"}


def severity_for(run_status: str, failure_count: int) -> str:
    """An ERROR outranks a FAILED: a check that could not run at all means the
    warehouse or the credentials are wrong, which blocks every other answer.
    Sustained failure escalates on its own - something broken for ten runs is
    not the same news as something broken once."""
    if run_status == RunStatus.ERROR.value:
        return IncidentSeverity.HIGH.value
    if failure_count >= 10:
        return IncidentSeverity.HIGH.value
    return IncidentSeverity.MEDIUM.value


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
    lines.append(f"DPM incident {incident.id} - updates will be added as comments.")

    return TicketContent(
        title=f"{check.name}: {'error' if run.status == RunStatus.ERROR.value else 'failure'} detected",
        description="\n".join(lines),
        priority=incident.severity,
        assignee=(rca or {}).get("suggestedOwner"),
        labels=("dpm", f"check-{check.type.lower().replace('_', '-')}"),
    )


def file_issue(incident: Incident, content: TicketContent) -> None:
    """File the incident's Jira issue and record the reference.

    Silent when there is no tracker: an unticketed incident is a normal
    state, not a failure. A failed *filing* is logged and left for the next
    sweep to retry, rather than faked with a local key that would never
    reconcile with anything in Jira.
    """
    backend = ticket_backend()
    if backend is None:
        return
    ref = backend.create(incident, content)
    if ref is None:
        return
    incident.ticket_key = ref.key
    incident.ticket_url = ref.url
    incident.ticket_status = settings.jira_reopen_status
    incident.ticket_synced_at = lifecycle.utcnow()
    incident.ticket_moved_at = lifecycle.utcnow()


def post_comment(incident: Incident, body: str) -> None:
    """Mirror a comment onto the Jira issue, if there is one."""
    backend = ticket_backend()
    if backend is not None and incident.ticket_key:
        backend.comment(incident, body)


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


def on_failed_run(
    db: Session, check: models.Check, run: models.CheckRun, rca: dict | None
) -> Incident:
    """A check just failed. Open, continue, or revive an incident for it."""
    incident = lifecycle.active_incident_for(db, check.id)

    if incident is None:
        revived = recently_cleared_incident(db, check.id)
        if revived is not None:
            body = (
                f"Failing again {_ago(revived)} after clearing - treating this as the same "
                f"incident recurring rather than a new one. {run.message or ''}"
            ).strip()
            lifecycle.reopen_incident(db, revived, body, check_run_id=run.id)
            revived.failure_count += 1
            revived.severity = severity_for(run.status, revived.failure_count)
            if revived.ticket_key:
                backend = ticket_backend()
                if backend is not None:
                    backend.transition(revived, done=False, comment=body)
            else:
                # The issue was never filed, or Jira only came online later.
                file_issue(revived, build_ticket_content(check, revived, run, rca))
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
        file_issue(incident, build_ticket_content(check, incident, run, rca))
        db.commit()
        return incident

    # Already open. Record it, and comment only when there is news - a
    # comment on every run of a check failing all day is how an issue becomes
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
        post_comment(incident, body)

    # A failure with no issue behind it - Jira was down when it opened, or
    # was configured afterwards. Retried here so the gap closes on its own.
    if not incident.ticket_key:
        file_issue(incident, build_ticket_content(check, incident, run, rca))

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
    backend = ticket_backend()
    if backend is not None and incident.ticket_key:
        backend.transition(incident, done=True, comment=body)
        incident.ticket_status = settings.jira_done_status
        incident.ticket_moved_at = lifecycle.utcnow()
    db.commit()
    return incident


def sync_ticket_state(db: Session, incidents: list[Incident]) -> int:
    """Refresh what Jira says about each incident's issue.

    Escalation asks "has anyone responded", and that is a fact about Jira,
    not about this database. Reading it once per sweep keeps the network
    call out of every individual decision - and keeps `ticket_moved_at`
    meaning what it says, which is when the *issue* last changed rather than
    when we last wrote to our own record.
    """
    backend = ticket_backend()
    if backend is None:
        return 0

    synced = 0
    for incident in incidents:
        if not incident.ticket_key:
            continue
        state = backend.fetch_state(incident)
        if state is None:
            continue
        if state.status != incident.ticket_status or state.assignee != incident.ticket_assignee:
            incident.ticket_moved_at = lifecycle.utcnow()
        incident.ticket_status = state.status
        incident.ticket_assignee = state.assignee
        incident.ticket_url = state.url or incident.ticket_url
        incident.ticket_synced_at = lifecycle.utcnow()
        synced += 1

    if synced:
        db.commit()
    return synced


def _is_untouched(incident: Incident) -> bool:
    """Whether the issue looks like nobody has picked it up.

    An incident with no issue counts as untouched: there is nothing for
    anyone to have responded to, which is exactly the case escalation should
    surface rather than skip.
    """
    if not incident.ticket_key:
        return True
    if incident.ticket_assignee:
        return False
    if not incident.ticket_status:
        return True
    return incident.ticket_status.strip().lower() in UNTOUCHED_STATUSES


def escalate_stale(db: Session) -> list[Incident]:
    """Poke the incidents nobody has responded to.

    This is the "not even being noticed" case, and it is about the human side
    rather than the pipeline: the check is still failing, the issue is still
    untouched, and silence has been read as resolution. Backed off so a
    long-running incident produces occasional escalations rather than a daily
    nag, which gets filtered and then ignored.
    """
    now = lifecycle.utcnow()
    stale_before = now - timedelta(hours=settings.escalate_after_hours)
    backoff_before = now - timedelta(hours=settings.escalation_backoff_hours)

    candidates = db.scalars(
        select(Incident)
        .where(
            Incident.state == IncidentState.OPEN.value,
            Incident.opened_at <= stale_before,
        )
        # Oldest first, so the cap below defers the least-neglected rather
        # than whichever happened to be scanned last.
        .order_by(Incident.opened_at)
    ).all()

    escalated: list[Incident] = []
    for incident in candidates:
        if len(escalated) >= settings.max_escalations_per_sweep:
            logger.info(
                "Escalation cap reached (%d); %d more will wait for the next sweep",
                settings.max_escalations_per_sweep,
                len(candidates) - len(escalated),
            )
            break
        if incident.last_escalated_at and incident.last_escalated_at > backoff_before:
            continue
        # Someone engaging with the issue is the signal to stop poking. The
        # point is absence of response, not slowness.
        if not _is_untouched(incident):
            continue
        # ...and if they engaged recently, give them the backoff window even
        # though the issue has drifted back to an untouched-looking status.
        if incident.ticket_moved_at and incident.ticket_moved_at > backoff_before:
            continue

        incident.escalation_count += 1
        incident.last_escalated_at = now
        if incident.severity != IncidentSeverity.CRITICAL.value:
            incident.severity = (
                IncidentSeverity.CRITICAL.value
                if incident.escalation_count >= 2
                else IncidentSeverity.HIGH.value
            )
        where = (
            f"the issue is still {incident.ticket_status or 'unstarted'}"
            if incident.ticket_key
            else "no issue was ever filed for it"
        )
        body = (
            f"No response after {_duration(incident)} and {incident.failure_count} failed "
            f"run(s); {where}. Raising priority to {incident.severity}."
        )
        lifecycle.add_event(db, incident, IncidentEventKind.ESCALATED, body)

        backend = ticket_backend()
        if backend is not None and incident.ticket_key:
            backend.set_priority(incident, incident.severity)
            backend.comment(incident, body)
        escalated.append(incident)

    if escalated:
        db.commit()
    return escalated


def reopen_closed_but_failing(db: Session) -> list[Incident]:
    """Closed is not the same as fixed.

    An issue marked done while its check is still failing is the most
    dangerous state the system can be in: everyone believes it is handled and
    nothing is watching. Reopening is the one place the monitor overrides a
    human decision, and it says so in the comment rather than doing it
    quietly.
    """
    reopened: list[Incident] = []
    candidates = db.scalars(
        select(Incident).where(Incident.state == IncidentState.CLEARED.value)
    ).all()

    for incident in candidates:
        # Only runs at or after the clear can say anything about whether the
        # closure was premature; earlier ones are the failures it was closed
        # for. `id` breaks ties on `started_at` so the answer is stable -
        # this query decides whether to override a person's decision to close
        # an issue, and that must not come down to row order.
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
            f"Reopening: this was closed but {incident.check.name} is still failing as "
            f"of the run at {latest.started_at:%Y-%m-%d %H:%M} UTC "
            f"({latest.message or 'no message'})."
        )
        lifecycle.reopen_incident(db, incident, body, check_run_id=latest.id)
        backend = ticket_backend()
        if backend is not None and incident.ticket_key:
            backend.transition(incident, done=False, comment=body)
            incident.ticket_status = settings.jira_reopen_status
            incident.ticket_moved_at = lifecycle.utcnow()
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
