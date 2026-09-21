"""The ticket-tracker interface the monitoring agents write through.

Same shape of decision as `connectors/registry.py`: the agents know how to
say "file this", "comment this", "close this", and nothing about where it
lands. A tracker is a backend, not a branch in the reporting logic.

Three operations, because they are the three the reporting agent actually
performs. Notably absent is "update the description" - a tracker is an
append-only conversation as far as this system is concerned, since rewriting
history under a human who is reading it is how automation loses trust.

Every method returns a `TicketRef` rather than raising on a tracker that is
merely unreachable. Reporting must degrade the way RCA does: a broken Jira
costs the external copy of the comment, never the incident record, which is
held in Postgres either way.
"""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from app import models


@dataclass
class TicketRef:
    """Where a ticket ended up, as far as the tracker is concerned."""

    key: str
    external_key: str | None = None
    external_url: str | None = None


@dataclass
class TicketContent:
    """What to file. Assembled by the caller so every backend renders the
    same facts, and so the wording is decided in one place rather than drifting
    between trackers."""

    title: str
    description: str
    priority: str
    assignee: str | None = None
    labels: tuple[str, ...] = ()


class TicketBackend(Protocol):
    """Implemented by the in-app board and by Jira."""

    name: str

    def create(
        self, db: Session, incident: models.Incident, content: TicketContent
    ) -> TicketRef:
        """File a new ticket for an incident."""
        ...

    def comment(self, db: Session, ticket: models.Ticket, body: str) -> str | None:
        """Add a comment. Returns the tracker's id for it, when it has one."""
        ...

    def transition(
        self, db: Session, ticket: models.Ticket, status: str, comment: str | None = None
    ) -> None:
        """Move a ticket to a new status, optionally with a closing comment."""
        ...
