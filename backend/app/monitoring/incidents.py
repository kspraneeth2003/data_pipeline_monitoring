"""Incident lifecycle: the primitives, with no judgement in them.

Everything here is deterministic and testable. Deciding *whether* an incident
should open, escalate or clear is a policy question, and it lives one layer up
in `triage.py` (rules) and `agent.py` (judgement). This module only knows how
to carry out a decision once made.

The split matters because the agent is optional. Both callers drive the same
primitives and write the same rows, so an incident opened by the rules is
indistinguishable in shape from one opened by the agent - only `triage_source`
differs, and that is recorded rather than inferred.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.models import (
    Incident,
    IncidentEvent,
    IncidentEventKind,
    IncidentState,
    TicketPriority,
    cuid,
)

# An incident clears after this many consecutive passes, not after one. A
# single green run of a flapping check is not a recovery, and closing on it
# produces a clear/reopen cycle that is worse than staying open - it teaches
# the reader that "cleared" means nothing.
CLEAR_AFTER_PASSES = 2

# Failures within this window of each other, on checks over the same database,
# are candidates for sharing one upstream cause. Deliberately generous: check
# schedules are staggered, so one dropped task surfaces over several minutes.
CORRELATION_WINDOW = timedelta(minutes=20)


def utcnow() -> datetime:
    """Naive UTC, matching the rest of the app's timestamps.

    The API returns naive UTC and the frontend parses it as such
    (`src/lib/time.ts`). A timezone-aware value here would serialize with an
    offset and shift every incident timestamp in the UI.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def active_incident_for(db: Session, check_id: str) -> Incident | None:
    """The open or warning incident for a check, if there is one.

    At most one is expected. If several exist - two sweeps racing, say - the
    oldest wins, so an incident's identity stays stable rather than jumping to
    a newer row and orphaning its ticket and comment history.
    """
    return db.scalars(
        select(Incident)
        .where(
            Incident.check_id == check_id,
            Incident.state.in_([IncidentState.OPEN.value, IncidentState.WARNING.value]),
        )
        .order_by(Incident.opened_at)
    ).first()


def add_event(
    db: Session,
    incident: Incident,
    kind: IncidentEventKind,
    body: str,
    author: str = "rule",
    check_run_id: str | None = None,
) -> IncidentEvent:
    """Append to an incident's comment stream.

    Not flushed or committed here: a caller usually writes an event and a state
    change together, and those must land as one unit or a reader can see a
    "cleared" comment on an incident that is still open.
    """
    event = IncidentEvent(
        id=cuid(),
        incident_id=incident.id,
        check_run_id=check_run_id,
        kind=kind.value,
        body=body,
        author=author,
    )
    db.add(event)
    return event


def find_correlation(db: Session, check: models.Check, now: datetime) -> str | None:
    """An existing correlation group this failure plausibly belongs to.

    Scoped to the same database and a short window, because that is the shape
    of a shared cause: one suspended task, one bad deploy, one warehouse
    outage takes out the checks over the objects it touched, at once.

    Deliberately conservative. A wrong grouping hides a second, unrelated
    failure inside someone else's ticket, which is worse than two tickets -
    so proximity in place *and* time is required, and the agent is left to
    make the harder calls.
    """
    siblings = db.scalars(
        select(Incident)
        .join(models.Check, models.Check.id == Incident.check_id)
        .where(
            models.Check.database_id == check.database_id,
            models.Check.id != check.id,
            Incident.state.in_([IncidentState.OPEN.value, IncidentState.WARNING.value]),
            Incident.opened_at >= now - CORRELATION_WINDOW,
        )
        .order_by(Incident.opened_at)
    ).all()

    for sibling in siblings:
        if sibling.correlation_id:
            return sibling.correlation_id
    if siblings:
        # First pairing in this group: mint an id and adopt the older
        # incident into it, so the group is never a single orphan member.
        correlation_id = cuid()
        siblings[0].correlation_id = correlation_id
        return correlation_id
    return None


def open_incident(
    db: Session,
    check: models.Check,
    run: models.CheckRun,
    title: str,
    summary: str | None,
    severity: str = TicketPriority.MEDIUM.value,
    triage_source: str = "rule",
    correlate: bool = True,
) -> Incident:
    now = utcnow()
    incident = Incident(
        id=cuid(),
        check_id=check.id,
        state=IncidentState.OPEN.value,
        severity=severity,
        title=title,
        summary=summary,
        opened_at=now,
        last_seen_at=now,
        first_run_id=run.id,
        last_run_id=run.id,
        failure_count=1,
        consecutive_passes=0,
        escalation_count=0,
        correlation_id=find_correlation(db, check, now) if correlate else None,
        triage_source=triage_source,
    )
    db.add(incident)
    db.flush()  # the event needs incident.id
    add_event(
        db,
        incident,
        IncidentEventKind.OPENED,
        summary or f"{check.name} started failing.",
        author=triage_source,
        check_run_id=run.id,
    )
    return incident


def record_failure(db: Session, incident: Incident, run: models.CheckRun) -> Incident:
    """Another failing run on an incident that is already open.

    Passes are reset rather than decremented: recovery has to be consecutive,
    or a check alternating pass/fail would creep to the clear threshold and
    close an incident that never stopped happening.
    """
    incident.failure_count += 1
    incident.consecutive_passes = 0
    incident.last_run_id = run.id
    incident.last_seen_at = utcnow()
    if incident.state == IncidentState.WARNING.value:
        incident.state = IncidentState.OPEN.value
    return incident


def record_pass(db: Session, incident: Incident, run: models.CheckRun) -> Incident:
    incident.consecutive_passes += 1
    incident.last_run_id = run.id
    incident.last_seen_at = utcnow()
    return incident


def clear_incident(
    db: Session,
    incident: Incident,
    body: str,
    author: str = "rule",
    check_run_id: str | None = None,
) -> Incident:
    incident.state = IncidentState.CLEARED.value
    incident.cleared_at = utcnow()
    add_event(db, incident, IncidentEventKind.CLEARED, body, author, check_run_id)
    return incident


def reopen_incident(
    db: Session,
    incident: Incident,
    body: str,
    author: str = "rule",
    check_run_id: str | None = None,
) -> Incident:
    """Bring a cleared incident back rather than opening a new one.

    Used when a check fails again shortly after clearing, and when a ticket is
    closed while its check is still failing. Reopening keeps the history in
    one place: the fact that this is the third recurrence is the most useful
    thing on the page, and a fresh incident throws it away.
    """
    incident.state = IncidentState.OPEN.value
    incident.cleared_at = None
    incident.consecutive_passes = 0
    incident.last_seen_at = utcnow()
    add_event(db, incident, IncidentEventKind.REOPENED, body, author, check_run_id)
    return incident


def suppress_incident(
    db: Session, incident: Incident, body: str, author: str = "rule"
) -> Incident:
    """Stop reporting on an incident, and say so.

    Suppression is a state with a reason attached, never a silent drop. An
    incident that stops producing comments for no stated reason is
    indistinguishable from the monitor being broken.
    """
    incident.state = IncidentState.SUPPRESSED.value
    add_event(db, incident, IncidentEventKind.SUPPRESSED, body, author)
    return incident
