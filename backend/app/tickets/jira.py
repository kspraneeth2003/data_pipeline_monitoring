"""Jira Cloud REST v3 - the only place tickets live.

Configured from `backend/.env` (`JIRA_BASE_URL`, `JIRA_EMAIL`,
`JIRA_API_TOKEN`, `JIRA_PROJECT_KEY`). With any of those unset there is no
tracker at all: incidents are still opened, tracked, escalated and cleared
here, they simply have no issue attached. That is a supported way to run
this, and `GET /api/monitor/status` says so plainly.

Two design points worth stating, because both look like missing features:

**Nothing raises.** A Jira that is down, rate-limited or misconfigured must
not cost the incident record. Every call catches, logs, and returns a value
saying the external write did not happen; the `IncidentEvent` in Postgres is
already written by the caller and remains the source of truth. Letting an
HTTP error escape into the sweep would mean one unreachable tracker stops
monitoring for every project.

**The description is Atlassian Document Format, not text.** v3 rejects a
plain string on `description`, which is the single most common way a first
Jira integration fails with an opaque 400.
"""

import base64
import logging

import httpx

from app import models
from app.config import settings
from app.tickets.base import TicketContent, TicketRef, TicketState

logger = logging.getLogger("dpm.tickets.jira")

TIMEOUT_SECONDS = 15

# Our severities -> Jira's default priority scheme. A site that renamed these
# will not match, which is why an unknown name is dropped rather than sent:
# Jira rejects the whole issue for one bad priority, and an issue filed at
# the wrong priority is worth far more than no issue at all.
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

    # --- transport ----------------------------------------------------

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict | None:
        try:
            response = httpx.request(
                method,
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
                "Jira %s %s failed: %s %s",
                method,
                path,
                error.response.status_code,
                error.response.text[:400],
            )
        except httpx.HTTPError as error:
            logger.warning("Jira %s %s unreachable: %s", method, path, error)
        return None

    # --- operations ---------------------------------------------------

    def create(self, incident: models.Incident, content: TicketContent) -> TicketRef | None:
        fields: dict = {
            "project": {"key": self.project_key},
            "summary": content.title[:255],
            "description": as_adf(content.description),
            "issuetype": {"name": settings.jira_issue_type},
            "labels": list(content.labels),
        }
        if priority := PRIORITY_MAP.get(content.priority):
            fields["priority"] = {"name": priority}

        created = self._request("POST", "/rest/api/3/issue", {"fields": fields})
        if not created or not created.get("key"):
            # The incident stays open and unticketed. The next sweep tries
            # again, which is why this returns None rather than inventing a
            # local key that would never reconcile with Jira.
            logger.warning("Could not file a Jira issue for incident %s", incident.id)
            return None

        key = created["key"]
        return TicketRef(key=key, url=f"{self.base_url}/browse/{key}")

    def comment(self, incident: models.Incident, body: str) -> bool:
        if not incident.ticket_key:
            return False
        created = self._request(
            "POST", f"/rest/api/3/issue/{incident.ticket_key}/comment", {"body": as_adf(body)}
        )
        return created is not None

    def fetch_state(self, incident: models.Incident) -> TicketState | None:
        """Read back what Jira currently says about the issue.

        This is what makes "nobody has responded" answerable. Only the two
        fields that decide it are requested, so the sweep's per-incident cost
        stays one small GET.
        """
        if not incident.ticket_key:
            return None
        issue = self._request(
            "GET", f"/rest/api/3/issue/{incident.ticket_key}?fields=status,assignee"
        )
        if not issue:
            return None
        fields = issue.get("fields") or {}
        status = ((fields.get("status") or {}).get("name")) or "Unknown"
        assignee = (fields.get("assignee") or {}).get("displayName")
        return TicketState(
            key=incident.ticket_key,
            status=status,
            assignee=assignee,
            url=f"{self.base_url}/browse/{incident.ticket_key}",
        )

    def set_priority(self, incident: models.Incident, priority: str) -> bool:
        name = PRIORITY_MAP.get(priority)
        if not incident.ticket_key or not name:
            return False
        updated = self._request(
            "PUT",
            f"/rest/api/3/issue/{incident.ticket_key}",
            {"fields": {"priority": {"name": name}}},
        )
        return updated is not None

    def transition(
        self, incident: models.Incident, done: bool, comment: str | None = None
    ) -> bool:
        if comment:
            self.comment(incident, comment)
        if not incident.ticket_key:
            return False

        target = settings.jira_done_status if done else settings.jira_reopen_status
        if not target:
            return False

        # Transition ids are per-workflow, so the target is looked up by name
        # rather than hardcoded - a board that renamed "Done" to "Complete"
        # would otherwise silently stop closing issues.
        listing = self._request(
            "GET", f"/rest/api/3/issue/{incident.ticket_key}/transitions"
        )
        if not listing:
            return False
        transitions = listing.get("transitions", [])

        match = next(
            (t for t in transitions if (t.get("to") or {}).get("name", "").lower() == target.lower()),
            None,
        ) or next((t for t in transitions if t.get("name", "").lower() == target.lower()), None)
        if not match:
            logger.warning(
                "No Jira transition to %r on %s (available: %s)",
                target,
                incident.ticket_key,
                [t.get("name") for t in transitions],
            )
            return False

        result = self._request(
            "POST",
            f"/rest/api/3/issue/{incident.ticket_key}/transitions",
            {"transition": {"id": match["id"]}},
        )
        return result is not None
