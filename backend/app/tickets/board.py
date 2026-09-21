"""The in-app Kanban board, as a ticket backend.

This is the default and the fallback. It is a real backend rather than a
stub: comments become `IncidentEvent` rows, which is where they live anyway,
so the board and Jira differ only in whether those events are also mirrored
somewhere a person's team already watches.
"""

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.models import IncidentEventKind
from app.monitoring.incidents import add_event
from app.tickets.base import TicketContent, TicketRef

PROJECT_PREFIX = "DPM"


def next_ticket_key(db: Session) -> str:
    # Best-effort sequential numbering - fine at this volume, would race under
    # real concurrency. A real tracker issues its own key and this is unused.
    for attempt in range(5):
        count = db.query(func.count(models.Ticket.id)).scalar() or 0
        candidate = f"{PROJECT_PREFIX}-{count + 1 + attempt}"
        if not db.query(models.Ticket).filter_by(key=candidate).first():
            return candidate
    import time

    return f"{PROJECT_PREFIX}-{int(time.time())}"


class BoardBackend:
    name = "board"

    def create(
        self, db: Session, incident: models.Incident, content: TicketContent
    ) -> TicketRef:
        # Only mints the key. Persisting the `Ticket` row is the caller's job
        # for every backend alike, so there is exactly one place a ticket is
        # written and no way for two backends to disagree about its shape.
        return TicketRef(key=next_ticket_key(db))

    def comment(self, db: Session, ticket: models.Ticket, body: str) -> str | None:
        # The incident's event stream *is* the board's comment thread, and the
        # caller has already written the event. Nothing further to mirror.
        return None

    def transition(
        self, db: Session, ticket: models.Ticket, status: str, comment: str | None = None
    ) -> None:
        ticket.status = status
        if comment and ticket.incident:
            add_event(db, ticket.incident, IncidentEventKind.COMMENT, comment, author="rule")
