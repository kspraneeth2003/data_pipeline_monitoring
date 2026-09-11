from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.models import cuid

PROJECT_PREFIX = "DPM"


def _priority_for_status(run_status: str) -> str:
    return "HIGH" if run_status == "ERROR" else "MEDIUM"


def _build_description(check_name: str, check_type: str, run_status: str, message: str, rca: dict) -> str:
    source = "LLM" if rca["evidence"]["source"] == "llm" else "heuristic"
    verb = "errored" if run_status == "ERROR" else "failed"
    return "\n".join(
        [
            f'Check "{check_name}" ({check_type}) {verb}.',
            "",
            f"Message: {message}",
            "",
            f"Root cause ({source} analysis, {round(rca['confidence'] * 100)}% confidence):",
            rca["rootCause"],
            "",
            f"Next steps: {rca['nextSteps']}",
        ]
    )


def _next_ticket_key(db: Session) -> str:
    # Best-effort sequential numbering - fine at this ticket volume, would
    # race under real concurrency (a real Jira integration wouldn't need this).
    for attempt in range(5):
        count = db.query(func.count(models.Ticket.id)).scalar() or 0
        candidate = f"{PROJECT_PREFIX}-{count + 1 + attempt}"
        exists = db.query(models.Ticket).filter_by(key=candidate).first()
        if not exists:
            return candidate
    import time

    return f"{PROJECT_PREFIX}-{int(time.time())}"


def create_mock_ticket(
    db: Session,
    check_run_id: str,
    check_name: str,
    check_type: str,
    run_status: str,
    message: str,
    rca: dict,
) -> models.Ticket:
    ticket = models.Ticket(
        id=cuid(),
        check_run_id=check_run_id,
        key=_next_ticket_key(db),
        title=f"{check_name}: {'error' if run_status == 'ERROR' else 'failure'} detected",
        description=_build_description(check_name, check_type, run_status, message, rca),
        priority=_priority_for_status(run_status),
        assignee=rca.get("suggestedOwner"),
        status="TODO",
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket
