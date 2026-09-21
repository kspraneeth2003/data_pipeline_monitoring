"""The issue-tracker interface the monitoring agent writes through.

Tickets live in Jira. This app does not have a ticket board and does not
store a copy of an issue - it holds an incident, and a reference to the Jira
issue that incident produced. Anything Jira knows about that issue (its
status, its assignee, its comment thread) belongs to Jira, and is read back
rather than mirrored.

Four operations, because they are the four the reporting agent performs:
file one, comment on one, move one, and read back whether anyone has touched
it. That last one is not incidental - "nobody has responded" is the judgement
that makes escalation worth having, and it is unanswerable without asking
Jira.

Nothing here raises. A tracker that is unreachable costs the external copy
of a comment, never the incident record, which is in Postgres either way.
Callers get a value saying what did or did not happen.
"""

from dataclasses import dataclass
from typing import Protocol

from app import models


@dataclass
class TicketRef:
    """A filed issue, as the tracker returned it."""

    key: str
    url: str | None = None


@dataclass
class TicketState:
    """What the tracker currently says about an issue.

    `status` is free text - Jira workflows are per-project and a site that
    renamed "Done" to "Shipped" is not misconfigured, it is normal.
    """

    key: str
    status: str
    assignee: str | None = None
    url: str | None = None


@dataclass
class TicketContent:
    """What to file. Assembled by the caller so the wording is decided in one
    place rather than drifting between trackers."""

    title: str
    description: str
    priority: str
    assignee: str | None = None
    labels: tuple[str, ...] = ()


class TicketBackend(Protocol):
    name: str

    def create(self, incident: models.Incident, content: TicketContent) -> TicketRef | None:
        """File an issue for an incident. None if it could not be filed."""
        ...

    def comment(self, incident: models.Incident, body: str) -> bool:
        """Add a comment to the incident's issue. False if it did not land."""
        ...

    def transition(self, incident: models.Incident, done: bool, comment: str | None = None) -> bool:
        """Move the issue to the done or the open end of its workflow."""
        ...

    def fetch_state(self, incident: models.Incident) -> TicketState | None:
        """Read the issue's current status and assignee. None if unreadable."""
        ...

    def set_priority(self, incident: models.Incident, priority: str) -> bool:
        """Raise or lower the issue's priority."""
        ...
