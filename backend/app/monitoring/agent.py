"""The reporting agent: judgement over the incidents the rules already track.

Built on `langchain.agents.create_agent`, which is a LangGraph agent loop -
model, tools, model, until it stops asking. The graph shape is not the
interesting part. What matters is the division of labour with `triage.py`:

    triage.py   deterministic, always runs, owns state transitions
    agent.py    judgement, optional, decides what is worth saying

The agent never writes to the database. It returns decisions, and
`apply_decision` applies the ones it is allowed to make, re-checking each
against the current row. That ordering is deliberate: an agent that writes
directly can do anything its worst response implies, whereas an agent that
proposes can only do what the applying code permits. Two of the six actions
are rejected outright here regardless of what the model asks for.

If the agent is unavailable, unparseable, or over budget, the sweep has
already done its deterministic passes and simply logs the failure. Reporting
degrades to the rules, exactly as RCA degrades to its heuristic.
"""

import logging
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.runner import build_agent, invoke_agent
from app.config import settings
from app.models import (
    Incident,
    IncidentEventKind,
    IncidentSeverity,
    IncidentState,
)
from app.monitoring import incidents as lifecycle
from app.monitoring.prompts import REPORTING_SYSTEM_PROMPT
from app.monitoring.tools import build_tools
from app.tickets.registry import ticket_backend

logger = logging.getLogger("dpm.monitoring.agent")

# How many incidents to put in front of the model at once. Enough that
# correlation is possible - the whole point of batching - and bounded so a
# project in a bad state cannot produce a prompt that costs more than it is
# worth. Beyond this the oldest are reviewed first; the rest wait a sweep.
MAX_INCIDENTS_PER_SWEEP = 25

VALID_SEVERITIES = {p.value for p in IncidentSeverity}


class IncidentDecision(BaseModel):
    """One decision about one incident."""

    incident_id: str = Field(description="The incident this decision is about.")
    action: Literal["NONE", "COMMENT", "ESCALATE", "WARN", "CLEAR", "SUPPRESS"]
    comment: str = Field(
        default="",
        description=(
            "What to post on the ticket. Required for every action except NONE. "
            "Plain sentences for an on-call engineer; lead with what changed and "
            "the numbers behind it."
        ),
    )
    severity: str | None = Field(
        default=None,
        description="LOW, MEDIUM, HIGH or CRITICAL. Only when it should change.",
    )
    correlation_group: str | None = Field(
        default=None,
        description=(
            "A short shared label for incidents you believe have one upstream "
            "cause. Use the same string across all of them."
        ),
    )
    reasoning: str = Field(
        description="Why you chose this action, for the people tuning the system."
    )


class ReportingDecisions(BaseModel):
    decisions: list[IncidentDecision]


def _incident_brief(incident: Incident) -> str:
    issue = (
        f"{incident.ticket_key}/{incident.ticket_status or 'unknown'}"
        if incident.ticket_key
        else "none"
    )
    return (
        f"- incident_id={incident.id} check={incident.check.name!r} "
        f"type={incident.check.type} state={incident.state} "
        f"severity={incident.severity} failures={incident.failure_count} "
        f"hours_open={round((lifecycle.utcnow() - incident.opened_at).total_seconds() / 3600, 1)} "
        f"jira={issue}"
    )


def build_prompt(incidents: list[Incident]) -> str:
    return (
        "Review these active incidents and decide what to do with each one.\n\n"
        + "\n".join(_incident_brief(i) for i in incidents)
        + "\n\nInvestigate with the tools before deciding. Return exactly one "
        "decision per incident listed above."
    )


def apply_decision(db: Session, incident: Incident, decision: IncidentDecision) -> bool:
    """Apply one decision, re-checking it against the incident as it is now.

    Returns whether anything was written. Every branch validates
    independently rather than trusting the model, because this function is
    the only thing standing between a bad response and the database.
    """
    action = decision.action
    if action == "NONE":
        return False

    if not decision.comment.strip():
        logger.warning(
            "Ignoring %s on %s: no comment supplied", action, incident.id
        )
        return False

    # CLEAR is refused. Whether a problem is over is a question about the
    # data, answered by the check itself passing - `triage.on_passed_run`
    # clears on consecutive passes. A model clearing an incident on
    # reasoning alone would close tickets on pipelines that are still broken,
    # which is the single worst thing this system could do.
    if action == "CLEAR":
        logger.info(
            "Refusing CLEAR on %s from the agent; only passing runs clear an "
            "incident. Recorded as a comment instead.",
            incident.id,
        )
        action = "COMMENT"

    # WARN is only meaningful on an incident that is not currently failing.
    # Downgrading an actively failing incident to WARNING would understate it.
    if action == "WARN" and incident.state == IncidentState.OPEN.value:
        if incident.consecutive_passes == 0:
            logger.info(
                "Refusing WARN on %s: the check is currently failing", incident.id
            )
            action = "COMMENT"

    backend = ticket_backend()

    if decision.severity in VALID_SEVERITIES:
        incident.severity = decision.severity
        if backend is not None and incident.ticket_key:
            backend.set_priority(incident, decision.severity)

    if decision.correlation_group:
        # Namespaced so a label the model reuses across sweeps cannot merge
        # two unrelated groups from different days.
        incident.correlation_id = f"agent:{decision.correlation_group}"[:200]

    kind = {
        "COMMENT": IncidentEventKind.COMMENT,
        "ESCALATE": IncidentEventKind.ESCALATED,
        "WARN": IncidentEventKind.COMMENT,
        "SUPPRESS": IncidentEventKind.SUPPRESSED,
    }[action]

    if action == "ESCALATE":
        incident.escalation_count += 1
        incident.last_escalated_at = lifecycle.utcnow()
    elif action == "WARN":
        incident.state = IncidentState.WARNING.value
    elif action == "SUPPRESS":
        incident.state = IncidentState.SUPPRESSED.value

    body = decision.comment.strip()
    lifecycle.add_event(
        db, incident, kind, body, author="agent", check_run_id=incident.last_run_id
    )
    incident.triage_source = "agent"

    # The incident record is already written. Mirroring to Jira is an extra
    # that may fail, and a tracker that is down must not cost the decision.
    if backend is not None and incident.ticket_key:
        if action == "SUPPRESS":
            # Suppression closes the issue, because leaving one open that
            # nothing will ever comment on again is worse than closing it
            # with a stated reason.
            backend.transition(incident, done=True, comment=body)
            incident.ticket_status = settings.jira_done_status
            incident.ticket_moved_at = lifecycle.utcnow()
        else:
            backend.comment(incident, body)
    return True


def review_incidents(db: Session, incidents: list[Incident]) -> int:
    """Have the agent review active incidents. Returns how many it acted on.

    Raises `AgentFailed` if the agent could not be reached or understood -
    the sweep catches it, having already completed its rule-based passes.
    """
    if not incidents:
        return 0

    batch = incidents[:MAX_INCIDENTS_PER_SWEEP]
    if len(incidents) > MAX_INCIDENTS_PER_SWEEP:
        logger.info(
            "Reviewing the %d oldest of %d active incidents this sweep",
            MAX_INCIDENTS_PER_SWEEP,
            len(incidents),
        )

    agent = build_agent(
        tools=build_tools(db),
        system_prompt=REPORTING_SYSTEM_PROMPT,
        response_format=ReportingDecisions,
        name="dpm-reporting-agent",
    )
    response = invoke_agent(agent, build_prompt(batch), ReportingDecisions)

    by_id = {incident.id: incident for incident in batch}
    applied = 0
    for decision in response.decisions:
        incident = by_id.get(decision.incident_id)
        if incident is None:
            # The model named something outside the batch. Dropped rather
            # than looked up: acting on an incident nobody asked about is
            # how a bounded review turns into an unbounded one.
            logger.warning("Agent returned unknown incident %s", decision.incident_id)
            continue
        try:
            if apply_decision(db, incident, decision):
                applied += 1
        except Exception:
            logger.exception("Could not apply agent decision to %s", incident.id)
            db.rollback()

    if applied:
        db.commit()
    return applied

