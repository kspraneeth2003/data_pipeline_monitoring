"""What the reporting agent can look at.

All read-only, all over the app's own database. The agent decides; the
caller writes. Nothing here touches Snowflake or a tracker, which keeps the
reporting agent's blast radius to "it read some rows and had an opinion".

The tools are built per sweep against one `Session` rather than defined at
import time, because a tool that opens its own session would leak one per
call and would not see the caller's uncommitted work.

Each returns plain JSON-serialisable data with units and names spelled out.
A model reading `{"missingInSilver": 412}` alongside a previous 37 can say
something useful; the same number as a bare column position cannot.
"""

import json
from datetime import timedelta

from langchain_core.tools import BaseTool, tool
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.models import Incident, IncidentState
from app.monitoring.incidents import utcnow

# How much history to hand over. Enough to see a trend and a flap, bounded so
# a check running every minute for a month cannot fill the context window.
RUN_HISTORY_LIMIT = 20
EVENT_HISTORY_LIMIT = 15


def _run_summary(run: models.CheckRun) -> dict:
    return {
        "run_id": run.id,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "duration_ms": run.duration_ms,
        "message": run.message,
        "metrics": run.metrics or {},
    }


def _incident_summary(incident: Incident) -> dict:
    return {
        "incident_id": incident.id,
        "check_name": incident.check.name,
        "check_type": incident.check.type,
        "database": incident.check.database.name if incident.check.database else None,
        "state": incident.state,
        "severity": incident.severity,
        "title": incident.title,
        "summary": incident.summary,
        "opened_at": incident.opened_at.isoformat(),
        "hours_open": round((utcnow() - incident.opened_at).total_seconds() / 3600, 1),
        "failure_count": incident.failure_count,
        "consecutive_passes": incident.consecutive_passes,
        "escalation_count": incident.escalation_count,
        "correlation_id": incident.correlation_id,
        # What Jira currently says, as of the sweep's refresh. Null means no
        # issue exists - Jira is unconfigured, or filing failed - which is
        # itself worth reporting rather than treating as "nobody responded".
        "jira_issue": (
            {
                "key": incident.ticket_key,
                "status": incident.ticket_status,
                "assignee": incident.ticket_assignee,
                # The number that answers "has anyone touched this?" -
                # measured from the issue's own last change in Jira, not
                # from our record, which moves for our own writes too.
                "hours_since_issue_changed": (
                    round((utcnow() - incident.ticket_moved_at).total_seconds() / 3600, 1)
                    if incident.ticket_moved_at
                    else None
                ),
            }
            if incident.ticket_key
            else None
        ),
    }


def build_tools(db: Session) -> list[BaseTool]:
    """Tools bound to one session, for one sweep."""

    @tool
    def get_incident(incident_id: str) -> str:
        """Full detail for one incident: its state, its ticket, and the
        comments already posted on it.

        Read this before commenting on an incident. The existing comments are
        what tell you whether something has already been said - repeating it
        is how a ticket becomes noise."""
        incident = db.get(Incident, incident_id)
        if incident is None:
            return json.dumps({"error": f"No incident {incident_id}"})
        detail = _incident_summary(incident)
        detail["events"] = [
            {
                "kind": event.kind,
                "author": event.author,
                "created_at": event.created_at.isoformat(),
                "body": event.body,
            }
            for event in incident.events[-EVENT_HISTORY_LIMIT:]
        ]
        return json.dumps(detail, default=str)

    @tool
    def get_run_history(incident_id: str) -> str:
        """The recent runs of the check behind an incident, newest first,
        with their metrics.

        This is how you tell a steady failure from one that is getting worse,
        and a real recurrence from a check that flaps between pass and fail.
        Compare the metrics across runs rather than reading only the latest."""
        incident = db.get(Incident, incident_id)
        if incident is None:
            return json.dumps({"error": f"No incident {incident_id}"})
        runs = db.scalars(
            select(models.CheckRun)
            .where(models.CheckRun.check_id == incident.check_id)
            .order_by(models.CheckRun.started_at.desc(), models.CheckRun.id.desc())
            .limit(RUN_HISTORY_LIMIT)
        ).all()
        statuses = [r.status for r in runs]
        return json.dumps(
            {
                "check_name": incident.check.name,
                "check_type": incident.check.type,
                "config": incident.check.config,
                # Pre-computed because it is the question most often asked of
                # this list, and counting transitions is exactly the kind of
                # arithmetic a model gets subtly wrong.
                "status_flips_in_window": sum(
                    1 for a, b in zip(statuses, statuses[1:]) if a != b
                ),
                "runs": [_run_summary(r) for r in runs],
            },
            default=str,
        )

    @tool
    def get_related_incidents(incident_id: str) -> str:
        """Other incidents active on the same project, with how far apart
        they opened.

        Use this to decide whether several failures are one upstream event -
        a suspended task or a bad deploy takes out every check over the
        objects it touched, within minutes of each other. Incidents that are
        already grouped share a correlation_id."""
        incident = db.get(Incident, incident_id)
        if incident is None:
            return json.dumps({"error": f"No incident {incident_id}"})
        project_id = incident.check.database.project_id

        siblings = db.scalars(
            select(Incident)
            .join(models.Check, models.Check.id == Incident.check_id)
            .join(models.Database, models.Database.id == models.Check.database_id)
            .where(
                models.Database.project_id == project_id,
                Incident.id != incident.id,
                Incident.state.in_(
                    [IncidentState.OPEN.value, IncidentState.WARNING.value]
                ),
            )
            .order_by(Incident.opened_at)
        ).all()

        return json.dumps(
            {
                "this_incident_opened_at": incident.opened_at.isoformat(),
                "related": [
                    {
                        **_incident_summary(s),
                        "minutes_apart": round(
                            abs((s.opened_at - incident.opened_at).total_seconds()) / 60, 1
                        ),
                    }
                    for s in siblings
                ],
            },
            default=str,
        )

    @tool
    def get_root_cause_analysis(incident_id: str) -> str:
        """The stored RCA for the run that opened an incident, if one exists:
        probable cause, confidence, and the git and Snowflake evidence behind
        it.

        Prefer citing this over speculating. A low confidence score means the
        evidence was thin, and should be reported as thin rather than
        restated as fact."""
        incident = db.get(Incident, incident_id)
        if incident is None or not incident.first_run_id:
            return json.dumps({"error": "No analysis available"})
        rca = db.scalars(
            select(models.RcaResult).where(
                models.RcaResult.check_run_id == incident.first_run_id
            )
        ).first()
        if rca is None:
            return json.dumps({"error": "No analysis was recorded for this incident"})
        return json.dumps(
            {
                "summary": rca.summary,
                "root_cause": rca.root_cause,
                "confidence": rca.confidence,
                "next_steps": rca.next_steps,
                "suggested_owner": rca.suggested_owner,
                "evidence": rca.evidence,
            },
            default=str,
        )

    @tool
    def get_check_incident_history(incident_id: str) -> str:
        """How often this same check has raised incidents before, and how
        long they lasted.

        A check that opens and clears repeatedly is noisy, and saying so is
        more useful than reporting its next failure as news."""
        incident = db.get(Incident, incident_id)
        if incident is None:
            return json.dumps({"error": f"No incident {incident_id}"})
        past = db.scalars(
            select(Incident)
            .where(Incident.check_id == incident.check_id, Incident.id != incident.id)
            .order_by(Incident.opened_at.desc())
            .limit(10)
        ).all()
        recent_cutoff = utcnow() - timedelta(days=7)
        return json.dumps(
            {
                "total_prior_incidents": len(past),
                "prior_incidents_last_7_days": sum(
                    1 for p in past if p.opened_at >= recent_cutoff
                ),
                "prior": [
                    {
                        "opened_at": p.opened_at.isoformat(),
                        "state": p.state,
                        "failure_count": p.failure_count,
                        "hours_to_clear": (
                            round((p.cleared_at - p.opened_at).total_seconds() / 3600, 1)
                            if p.cleared_at
                            else None
                        ),
                    }
                    for p in past
                ],
            },
            default=str,
        )

    return [
        get_incident,
        get_run_history,
        get_related_incidents,
        get_root_cause_analysis,
        get_check_incident_history,
    ]
