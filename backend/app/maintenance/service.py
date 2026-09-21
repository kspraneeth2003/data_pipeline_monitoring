"""Detect a change, re-derive, reconcile.

The order mirrors ingestion: rules first and always, agent second and
optional. With no model configured this still does something useful -
it re-derives from the new DDL and records a revision wherever the derived
config differs from the stored one, which catches the mechanical cases (a
filter appeared, a threshold moved) without any judgement at all.

What it cannot do without the agent is tell a rename from a replacement, so
every rule-made revision is PENDING. Nothing is auto-applied on a diff the
rules merely noticed differed.
"""

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.ingest.ddl_parser import parse_repo
from app.ingest.heuristic import CheckProposal, propose_checks
from app.ingest.repo import fetch_repo
from app.maintenance.detector import ChangeReport, detect_changes
from app.models import CheckRevision, RevisionKind, RevisionStatus, cuid
from app.monitoring.incidents import utcnow

logger = logging.getLogger("dpm.maintenance")


def _derived_for(project: models.Project, checkout_root: Path) -> list[CheckProposal]:
    parsed = parse_repo(checkout_root)
    return propose_checks(parsed)


def _existing_revision(db: Session, check_id: str, config: dict) -> CheckRevision | None:
    """A pending or already-rejected revision proposing this same config.

    Without this the detector re-proposes an identical change every time it
    runs, and a rejected proposal comes back forever - which is how a review
    queue becomes something people stop opening.
    """
    return db.scalars(
        select(CheckRevision).where(
            CheckRevision.check_id == check_id,
            CheckRevision.status.in_(
                [RevisionStatus.PENDING.value, RevisionStatus.REJECTED.value]
            ),
        )
    ).first()


def reconcile_by_rules(
    db: Session, project: models.Project, report: ChangeReport, derived: list[CheckProposal]
) -> list[CheckRevision]:
    """The no-model path: propose an update wherever a derived config
    differs from what is stored, matched on the objects a check points at.

    Deliberately narrow. It only matches a derived proposal to an existing
    check when they describe the same objects, and it never proposes
    retiring or creating anything, because both of those need the judgement
    it does not have. Its value is catching the mechanical drift - a filter
    that appeared, a freshness threshold that moved - that would otherwise
    sit unnoticed until the check started failing.
    """
    revisions: list[CheckRevision] = []
    existing = [c for database in project.databases for c in database.checks]

    for proposal in derived:
        target = _match_existing(existing, proposal)
        if target is None or not target.agent_may_rewrite:
            continue
        if target.config == proposal["config"]:
            continue
        if _existing_revision(db, target.id, proposal["config"]):
            continue

        revision = CheckRevision(
            id=cuid(),
            check_id=target.id,
            database_id=target.database_id,
            kind=RevisionKind.UPDATE.value,
            status=RevisionStatus.PENDING.value,
            current_config=dict(target.config),
            proposed_name=proposal["name"],
            proposed_type=proposal["type"],
            proposed_schedule=proposal["schedule"],
            proposed_config=proposal["config"],
            reason=(
                "Re-derived from the changed DDL: the generated config no longer matches "
                f"the stored one. {proposal['rationale']} "
                "Proposed by rule without an agent, so it has not been checked for whether "
                "this is a rename or a replacement - review before applying."
            ),
            confidence=None,
            detected_change={
                "changed_paths": report.repo.changed_paths if report.repo else [],
                "subjects": report.repo.subjects if report.repo else [],
            },
            triggered_by_commit=report.repo.to_commit if report.repo else None,
        )
        db.add(revision)
        revisions.append(revision)

    db.commit()
    return revisions


def _match_existing(
    checks: list[models.Check], proposal: CheckProposal
) -> models.Check | None:
    """Pair a derived proposal with the check it supersedes, by type and by
    the objects it names. Matching on name would break the moment a name is
    edited, which is exactly when the pairing matters most."""
    objects = _objects_of(proposal["type"], proposal["config"])
    if not objects:
        return None
    for check in checks:
        if check.type != proposal["type"]:
            continue
        if _objects_of(check.type, check.config) == objects:
            return check
    return None


def _objects_of(check_type: str, config: dict) -> tuple:
    if check_type == "BRONZE_TO_SILVER_PARITY":
        return (config.get("bronzeObject"), config.get("silverObject"))
    if check_type == "NULL_RATE":
        return (config.get("object"), config.get("column"))
    return (config.get("object"), config.get("comparisonObject"))


def run_maintenance(
    db: Session, project: models.Project, include_warehouse: bool = True
) -> dict:
    """One maintenance pass over one project."""
    result: dict = {
        "project": project.name,
        "changed": False,
        "revisions": 0,
        "auto_applied": 0,
        "undocumented_ddl": 0,
        "errors": [],
        "agent_error": None,
    }

    report = detect_changes(db, project, include_warehouse=include_warehouse)
    result["errors"] = report.errors
    result["undocumented_ddl"] = len(report.undocumented)

    if not report.has_changes:
        return result
    result["changed"] = True

    if not (report.repo and report.repo.is_change):
        # Warehouse-only change. There is no new DDL to re-derive from, so
        # the finding is the drift itself, reported by the caller.
        return result

    checkout = fetch_repo(project.repo_url, None, project.repo_ref)
    checkout_root = Path(checkout["path"])
    derived = _derived_for(project, checkout_root)

    revisions: list[CheckRevision] = []
    if settings.agent_enabled:
        try:
            from app.maintenance.agent import reconcile

            diff_text = _diff_text(checkout_root, report)
            revisions = reconcile(db, project, report, derived, checkout_root, diff_text)
        except Exception as error:  # noqa: BLE001
            logger.exception("Maintenance agent failed for %s", project.name)
            db.rollback()
            result["agent_error"] = str(error)[:300]

    if not revisions:
        revisions = reconcile_by_rules(db, project, report, derived)

    result["revisions"] = len(revisions)
    result["auto_applied"] = sum(
        1 for r in revisions if r.status == RevisionStatus.AUTO_APPLIED.value
    )

    # Move the project's watermark forward so the same commit range is not
    # re-examined next pass. Done last: if anything above raised, the
    # watermark stays put and the change is picked up again.
    project.repo_commit = report.repo.to_commit
    project.updated_at = utcnow()
    db.commit()
    return result


def run_maintenance_all(db: Session) -> list[dict]:
    """A maintenance pass over every project that has a repository."""
    projects = db.scalars(
        select(models.Project).where(models.Project.repo_url.is_not(None))
    ).all()
    results = []
    for project in projects:
        try:
            results.append(run_maintenance(db, project, settings.maintenance_scan_warehouse))
        except Exception as error:  # noqa: BLE001
            # One project's unreachable repo must not stop the others.
            logger.exception("Maintenance failed for %s", project.name)
            db.rollback()
            results.append({"project": project.name, "errors": [str(error)[:300]]})
    return results


def maintenance_job() -> None:
    """Scheduler entry point. Never raises, for the same reason the monitor
    sweep does not: an exception escaping kills the job and the failure is
    silent until the process restarts."""
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        results = run_maintenance_all(db)
        changed = [r for r in results if r.get("changed")]
        if changed:
            logger.info("Maintenance found changes in %d project(s): %s", len(changed), changed)
    except Exception:
        logger.exception("Maintenance pass failed")
    finally:
        db.close()


def _diff_text(root: Path, report: ChangeReport) -> str:
    import subprocess

    if not report.repo or not report.repo.from_commit:
        return ""
    try:
        return subprocess.run(
            ["git", "diff", f"{report.repo.from_commit}..{report.repo.to_commit}"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
    except (subprocess.SubprocessError, OSError) as error:
        logger.warning("Could not read diff text: %s", error)
        return ""
