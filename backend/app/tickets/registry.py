"""Picks the issue tracker from configuration, or nothing at all.

Returns None when Jira is not fully configured, and that is a first-class
state rather than an error: incidents are opened, commented, escalated and
cleared in this app regardless. Jira is where those get a face the rest of
the organisation already watches.

Every caller checks for None. There is deliberately no null-object tracker
that quietly swallows writes - a backend that accepts a comment and drops it
is indistinguishable from one that works, which is the failure mode this
whole file exists to avoid.
"""

import logging
from functools import lru_cache

from app.config import settings
from app.tickets.base import TicketBackend

logger = logging.getLogger("dpm.tickets")


@lru_cache(maxsize=1)
def ticket_backend() -> TicketBackend | None:
    if not settings.jira_configured:
        logger.info("Jira is not configured; incidents will be tracked without issues")
        return None
    from app.tickets.jira import JiraBackend

    logger.info("Filing issues to Jira project %s", settings.jira_project_key)
    return JiraBackend()
