"""Picks the ticket backend from configuration.

Same pattern as `connectors/registry.py`: one place decides, and nothing
downstream branches on which tracker is in use. Jira is selected only when
every credential it needs is present - a half-configured tracker silently
dropping tickets is worse than an unconfigured one visibly using the board.
"""

import logging
from functools import lru_cache

from app.config import settings
from app.tickets.base import TicketBackend
from app.tickets.board import BoardBackend

logger = logging.getLogger("dpm.tickets")


@lru_cache(maxsize=1)
def ticket_backend() -> TicketBackend:
    if settings.jira_configured:
        from app.tickets.jira import JiraBackend

        logger.info("Filing tickets to Jira project %s", settings.jira_project_key)
        return JiraBackend()
    return BoardBackend()
