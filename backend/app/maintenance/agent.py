"""The maintenance agent: keeping checks correct as the pipeline changes.

Runs when `detector.py` sees the DDL move. The rules re-derive what the
checks would be from the new DDL; this agent reconciles that against what
exists, and writes `CheckRevision` rows.

It never edits a check. Every outcome is a revision, and only revisions
against a check the agent itself derived - which nobody has since edited -
are applied without review. Everything else waits for a person.

That boundary is enforced here, in `_may_auto_apply`, not only in the
prompt. The prompt tells the agent what it should not touch; this decides
what it *can*. A prompt is guidance and a model can misread it; the check on
`agent_may_rewrite` cannot be argued with. It matters because of a specific
failure this repo already documents: a check derived from a MERGE asserts
that the MERGE did what the MERGE says, so when the inventory MERGE reads
`RAW_PAYLOAD:warehouse` into `WAREHOUSE_ID` the derived check agrees with it
and passes. The hand-written check on the same tables fails, because a
person wrote the intent. Letting the agent overwrite that would remove the
only thing in the system that catches it.
"""

import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app import models
from app.agents.runner import build_agent, invoke_agent
from app.checks.config_schemas import CONFIG_SCHEMAS_BY_TYPE
from app.ingest.heuristic import CheckProposal
from app.maintenance.detector import ChangeReport
from app.maintenance.prompts import MAINTENANCE_SYSTEM_PROMPT
from app.maintenance.tools import build_tools
from app.checks.stage import derive_stage, stage_for
from app.checks.versions import record_version
from app.models import CheckOrigin, CheckRevision, RevisionKind, RevisionStatus, cuid

logger = logging.getLogger("dpm.maintenance.agent")

# Below this, the agent said it was guessing. A guess is reviewed, never
# applied, whatever the check's provenance.
AUTO_APPLY_MIN_CONFIDENCE = 0.75


class CheckDecision(BaseModel):
    """What should happen to one check, or one check that should exist."""

    action: Literal["KEEP", "UPDATE", "RETIRE", "CREATE"]
    check_id: str | None = Field(
        default=None,
        description="The existing check. Null only for CREATE.",
    )
    reason: str = Field(
        description=(
            "For a reviewer who knows the pipeline but has not read the diff: "
            "what changed in the DDL, what it means for this check, and what "
            "you propose. Cite the file and the column or clause."
        )
    )
    confidence: float = Field(
        ge=0, le=1, description="Below 0.5 means you are guessing."
    )
    proposed_name: str | None = None
    proposed_type: str | None = None
    proposed_schedule: str | None = None
    proposed_config: dict | None = Field(
        default=None, description="Required for UPDATE and CREATE. Must be valid for the type."
    )
    database: str | None = Field(
        default=None, description="Database name. Required for CREATE."
    )


class MaintenanceDecisions(BaseModel):
    decisions: list[CheckDecision]
    summary: str = Field(description="One sentence on what this change means overall.")


def _may_auto_apply(check: models.Check | None, decision: CheckDecision) -> bool:
    """Whether this revision can be applied without a person looking.

    Four conditions, all required. Any one of them failing means the
    revision is still written - it is simply written as PENDING.
    """
    if decision.action == "CREATE":
        # A new check is always reviewed. Nothing existed to be wrong before,
        # so nothing is broken by waiting, and a spurious check that starts
        # failing on its own schedule is expensive to trace back.
        return False
    if check is None:
        return False
    if not check.agent_may_rewrite:
        return False
    return decision.confidence >= AUTO_APPLY_MIN_CONFIDENCE


def _validate_config(check_type: str, config: dict) -> str | None:
    """None if the config is valid for its type, else why not."""
    schema = CONFIG_SCHEMAS_BY_TYPE.get(check_type)
    if schema is None:
        return f"unknown check type {check_type!r}"
    try:
        schema.model_validate(config)
    except ValidationError as error:
        return str(error)[:300]
    return None


def build_prompt(report: ChangeReport, checks: list[models.Check]) -> str:
    lines = [
        f"The DDL for project {report.project_name!r} has changed. "
        "Decide what should happen to each of its checks.",
        "",
    ]
    if report.repo:
        lines.append(
            f"Commits {report.repo.from_commit or '(unknown)'}..{report.repo.to_commit} "
            f"touched {len(report.repo.changed_paths)} watched file(s)."
        )
        for subject in report.repo.subjects[:20]:
            lines.append(f"  - {subject}")
    if report.undocumented:
        lines += [
            "",
            "The warehouse also shows DDL with no commit explaining it:",
            *(
                f"  - {c.query_type} by {c.user_name} at {c.executed_at}"
                for c in report.undocumented[:10]
            ),
        ]
    lines += [
        "",
        f"There are {len(checks)} existing checks. Use the tools to read them, the "
        "freshly derived set, the diff, and any file you need.",
        "Return a decision for every existing check, plus CREATE entries for "
        "anything genuinely new.",
    ]
    return "\n".join(lines)


def record_revision(
    db: Session,
    project: models.Project,
    check: models.Check | None,
    decision: CheckDecision,
    report: ChangeReport,
) -> CheckRevision | None:
    """Turn one decision into a revision row, applying it if permitted."""
    if decision.action == "KEEP":
        return None

    database = check.database if check else _database_named(project, decision.database)
    if database is None:
        logger.warning(
            "Dropping %s decision: no database (%r)", decision.action, decision.database
        )
        return None

    check_type = decision.proposed_type or (check.type if check else None)
    if decision.action in ("UPDATE", "CREATE"):
        if not decision.proposed_config or not check_type:
            logger.warning("Dropping %s decision with no config", decision.action)
            return None
        # Validated against the same schema the API enforces. An invalid
        # proposal would error on first run, which reads as a broken
        # pipeline rather than a bad suggestion.
        if problem := _validate_config(check_type, decision.proposed_config):
            logger.warning("Dropping %s decision: invalid config (%s)", decision.action, problem)
            return None

    revision = CheckRevision(
        id=cuid(),
        check_id=check.id if check else None,
        database_id=database.id,
        kind=RevisionKind[decision.action if decision.action != "RETIRE" else "RETIRE"].value,
        status=RevisionStatus.PENDING.value,
        current_config=dict(check.config) if check else None,
        proposed_name=decision.proposed_name,
        proposed_type=check_type,
        proposed_schedule=decision.proposed_schedule,
        proposed_config=decision.proposed_config,
        reason=decision.reason,
        confidence=decision.confidence,
        detected_change={
            "changed_paths": report.repo.changed_paths if report.repo else [],
            "subjects": report.repo.subjects if report.repo else [],
            "undocumented_warehouse_ddl": [
                {"type": c.query_type, "user": c.user_name, "at": c.executed_at}
                for c in report.undocumented
            ],
        },
        triggered_by_commit=report.repo.to_commit if report.repo else None,
    )
    db.add(revision)

    if _may_auto_apply(check, decision):
        apply_revision(db, revision, auto=True)
    return revision


def apply_revision(db: Session, revision: CheckRevision, auto: bool = False) -> None:
    """Write a revision onto its check.

    Shared by the auto-apply path and the review endpoint, so a revision a
    person accepts is applied by exactly the same code as one the agent
    applied itself - there is no second, less-tested path.
    """
    from app.monitoring.incidents import utcnow

    check = revision.check
    if revision.kind == RevisionKind.RETIRE.value and check:
        # Disabled, not deleted. The run history is evidence about a pipeline
        # that really did behave that way, and a retired check that turns out
        # to matter can be switched back on.
        check.enabled = False
    elif revision.kind == RevisionKind.UPDATE.value and check:
        if revision.proposed_name:
            check.name = revision.proposed_name
        if revision.proposed_schedule:
            check.schedule = revision.proposed_schedule
        if revision.proposed_config:
            check.config = revision.proposed_config
        check.origin = CheckOrigin.AGENT.value
        check.derived_at_commit = revision.triggered_by_commit
        check.stage = stage_for(check.type, check.config, check.stage, check.stage_locked)
        # The agent's edits are versioned on the same terms as a person's.
        # An agent-applied change is exactly the kind somebody later needs to
        # see the before-image of, since nobody watched it happen.
        record_version(db, check, author="agent", note=revision.reason)
    elif revision.kind == RevisionKind.CREATE.value:
        database = revision.database
        db.add(
            models.Check(
                id=cuid(),
                name=revision.proposed_name or "Proposed check",
                type=revision.proposed_type or "ROW_COUNT",
                schedule=revision.proposed_schedule or "*/30 * * * *",
                database_id=database.id,
                connector_id=database.connector_id,
                config=revision.proposed_config or {},
                origin=CheckOrigin.AGENT.value,
                derived_at_commit=revision.triggered_by_commit,
                enabled=True,
                stage=derive_stage(
                    revision.proposed_type or "ROW_COUNT", revision.proposed_config or {}
                ),
            )
        )

    revision.status = (
        RevisionStatus.AUTO_APPLIED.value if auto else RevisionStatus.APPLIED.value
    )
    revision.reviewed_at = utcnow()


def _database_named(project: models.Project, name: str | None) -> models.Database | None:
    if not name:
        return project.databases[0] if len(project.databases) == 1 else None
    return next(
        (d for d in project.databases if d.name.upper() == name.upper()), None
    )


def reconcile(
    db: Session,
    project: models.Project,
    report: ChangeReport,
    derived: list[CheckProposal],
    checkout_root: Path,
    diff_text: str,
) -> list[CheckRevision]:
    """Have the agent reconcile the derived set against the existing checks.

    Raises `AgentFailed` if the agent could not be reached or understood.
    The caller records that and leaves the checks untouched - stale checks
    are recoverable, checks changed on a half-understood response are not.
    """
    checks = [check for database in project.databases for check in database.checks]

    agent = build_agent(
        tools=build_tools(
            db,
            project,
            checkout_root,
            derived,
            report.repo.changed_paths if report.repo else [],
            diff_text,
        ),
        system_prompt=MAINTENANCE_SYSTEM_PROMPT,
        response_format=MaintenanceDecisions,
        name="dpm-maintenance-agent",
    )
    response = invoke_agent(agent, build_prompt(report, checks), MaintenanceDecisions)

    by_id = {check.id: check for check in checks}
    revisions: list[CheckRevision] = []
    for decision in response.decisions:
        check = by_id.get(decision.check_id) if decision.check_id else None
        if decision.action != "CREATE" and check is None:
            logger.warning("Agent named unknown check %s", decision.check_id)
            continue
        revision = record_revision(db, project, check, decision, report)
        if revision is not None:
            revisions.append(revision)

    db.commit()
    return revisions
