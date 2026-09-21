"""Jira Cloud REST v3, as a ticket backend.

Configured entirely from `backend/.env` (`JIRA_BASE_URL`, `JIRA_EMAIL`,
`JIRA_API_TOKEN`, `JIRA_PROJECT_KEY`). With any of those unset the registry
never constructs this class, and the board backend is used instead - so the
app runs identically with and without a Jira to talk to.

Two design points worth stating, because both look like missing features:

**Nothing raises.** A Jira that is down, rate-limited or misconfigured must
not cost the incident record. Every call catches, logs, and returns a value
saying the external copy did not happen; the `IncidentEvent` in Postgres has
already been written by the caller and remains the source of truth. The
alternative - letting an HTTP error escape into the sweep - would mean one
unreachable tracker stops monitoring for every project.

**The description is Atlassian Document Format, not text.** v3 rejects a
plain string on `description`, which is the single most common way a first
Jira integration fails with an opaque 400.
"""

import base64
import logging

import httpx

from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.tickets.base import TicketContent, TicketRef

logger = logging.getLogger("dpm.tickets.jira")

TIMEOUT_SECONDS = 15

# Our severities -> Jira's default priority scheme. A site that renamed these
# will not match, which is why an unknown name is dropped rather than sent:
# Jira rejects the whole issue for one bad priority, and a ticket filed at the
# wrong priority is worth far more than no ticket at all.
PRIORITY_MAP = {
    "CRITICAL": "Highest",
    "HIGH": "High",
    "MEDIUM": "Medium",
    "LOW": "Low",
}


def as_adf(text: str) -> dict:
    """Plain text -> Atlassian Document Format, one paragraph per line.

    Blank lines are dropped: ADF rejects a paragraph with an empty text node,
    which turns a harmlessly double-spaced description into a 400.
    """
    paragraphs = [
        {"type": "paragraph", "content": [{"type": "text", "text": line}]}
        for line in text.splitlines()
        if line.strip()
    ] or [{"type": "paragraph", "content": [{"type": "text", "text": text or " "}]}]
    return {"type": "doc", "version": 1, "content": paragraphs}


class JiraBackend:
    name = "jira"

    def __init__(self) -> None:
        self.base_url = settings.jira_base_url.rstrip("/")
        self.project_key = settings.jira_project_key
        token = f"{settings.jira_email}:{settings.jira_api_token}".encode()
        self._headers = {
            "Authorization": f"Basic {base64.b64encode(token).decode()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, payload: dict) -> dict | None:
        try:
            response = httpx.post(
                f"{self.base_url}{path}",
                json=payload,
                headers=self._headers,
                timeout=TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return response.json() if response.content else {}
        except httpx.HTTPStatusError as error:
            # Jira puts the actual reason in the body; the status line alone
            # ("400 Bad Request") is useless for diagnosing a field mapping.
            logger.warning(
                "Jira %s failed: %s %s", path, error.response.status_code, error.response.text[:400]
            )
        except httpx.HTTPError as error:
            logger.warning("Jira %s unreachable: %s", path, error)
        return None

    def create(
        self, db: Session, incident: models.Incident, content: TicketContent
    ) -> TicketRef:
        fields: dict = {
            "project": {"key": self.project_key},
            "summary": content.title[:255],
            "description": as_adf(content.description),
            "issuetype": {"name": settings.jira_issue_type},
            "labels": list(content.labels),
        }
        if priority := PRIORITY_MAP.get(content.priority):
            fields["priority"] = {"name": priority}

        created = self._post("/rest/api/3/issue", {"fields": fields})
        if not created:
            # Fall back to a board ticket rather than losing the incident's
            # only human-visible artifact.
            from app.tickets.board import next_ticket_key

            logger.warning("Filing incident %s on the local board instead", incident.id)
            return TicketRef(key=next_ticket_key(db))

        key = created.get("key", "")
        return TicketRef(
            key=key,
            external_key=key,
            external_url=f"{self.base_url}/browse/{key}" if key else None,
        )

    def comment(self, db: Session, ticket: models.Ticket, body: str) -> str | None:
        if not ticket.external_key:
            return None
        created = self._post(
            f"/rest/api/3/issue/{ticket.external_key}/comment", {"body": as_adf(body)}
        )
        return created.get("id") if created else None

    def transition(
        self, db: Session, ticket: models.Ticket, status: str, comment: str | None = None
    ) -> None:
        ticket.status = status
        if comment:
            self.comment(db, ticket, comment)
        if not ticket.external_key:
            return

        # Transition ids are per-workflow, so the target has to be looked up
        # by name rather than hardcoded - the same board renamed "Done" to
        # "Complete" and a hardcoded id would silently stop closing tickets.
        target = settings.jira_done_status if status == "DONE" else None
        if not target:
            return
        try:
            response = httpx.get(
                f"{self.base_url}/rest/api/3/issue/{ticket.external_key}/transitions",
                headers=self._headers,
                timeout=TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            transitions = response.json().get("transitions", [])
        except httpx.HTTPError as error:
            logger.warning("Could not read Jira transitions: %s", error)
            return

        match = next(
            (t for t in transitions if t.get("to", {}).get("name", "").lower() == target.lower()),
            None,
        ) or next(
            (t for t in transitions if t.get("name", "").lower() == target.lower()), None
        )
        if not match:
            logger.warning(
                "No Jira transition to %r on %s (available: %s)",
                target,
                ticket.external_key,
                [t.get("name") for t in transitions],
            )
            return
        self._post(
            f"/rest/api/3/issue/{ticket.external_key}/transitions",
            {"transition": {"id": match["id"]}},
        )
