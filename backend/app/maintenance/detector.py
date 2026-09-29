"""Noticing that the pipeline's definition moved.

Two independent signals, because a pipeline can change in two places:

**The repository.** New commits touching the files a project's databases were
derived from. Authoritative for what the pipeline is *supposed* to be, and
cheap to check - the repo is already cloned in `.repo-cache/`.

**The warehouse.** DDL statements in Snowflake's QUERY_HISTORY - ALTER TABLE,
CREATE/ALTER TASK, CREATE STREAM - plus LAST_ALTERED on the objects checks
point at. Authoritative for what the pipeline *is*.

When the two disagree, that is itself the finding. A table altered in
production with no commit behind it is undocumented drift: nothing in the
repo explains the shape the data now has, so every check derived from the
repo is asserting a contract nobody agreed to. It is reported whether or not
any check has failed, because by the time one fails the change is old news.

The warehouse side is best-effort. It needs a live connection per project,
and a warehouse that is unreachable must not stop the repo side from
working - a detector that only runs when everything is healthy is a detector
that never runs when it matters.
"""

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app import models
from app.connectors.registry import build_connector
from app.ingest.repo import fetch_repo
from app.rca.snowflake_context import DDL_QUERY_TYPES

logger = logging.getLogger("dpm.maintenance.detector")

# How far back to look for warehouse DDL. INFORMATION_SCHEMA.QUERY_HISTORY is
# scoped to about seven days, so asking for more returns nothing extra.
WAREHOUSE_LOOKBACK_DAYS = 7


@dataclass
class RepoChange:
    """A commit range that touched files a project's checks were derived from."""

    from_commit: str | None
    to_commit: str
    changed_paths: list[str]
    subjects: list[str]

    @property
    def is_change(self) -> bool:
        return bool(self.changed_paths)


@dataclass
class WarehouseChange:
    """A DDL statement observed in the warehouse."""

    object_name: str
    query_type: str
    executed_at: str
    user_name: str
    statement: str


@dataclass
class ChangeReport:
    project_id: str
    project_name: str
    repo: RepoChange | None = None
    warehouse: list[WarehouseChange] = field(default_factory=list)
    # Objects the warehouse says changed with no commit explaining it.
    undocumented: list[WarehouseChange] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool((self.repo and self.repo.is_change) or self.warehouse)


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {args[0]} failed")
    return result.stdout


def watched_paths(project: models.Project) -> set[str]:
    """The repo files this project's checks were derived from.

    Taken from `Database.repo_paths`, which ingestion recorded per schema.
    A project whose databases have no paths - a hand-built one, or one that
    predates ingestion - watches nothing, and the detector correctly reports
    no repo changes rather than guessing at which files matter.
    """
    return {
        path
        for database in project.databases
        for path in (database.repo_paths or {}).values()
        if path
    }


def detect_repo_change(project: models.Project) -> RepoChange | None:
    """Fetch the project's repo and diff it against the commit checks were
    derived at. None when the project has no repository."""
    if not project.repo_url:
        return None

    checkout = fetch_repo(project.repo_url, None, project.repo_ref)
    root = Path(checkout["path"])
    head = checkout["commit"]
    previous = project.repo_commit

    if not previous or previous == head:
        return RepoChange(from_commit=previous, to_commit=head, changed_paths=[], subjects=[])

    try:
        diff = _git(["diff", "--name-only", f"{previous}..{head}"], root)
        log = _git(["log", "--format=%s", f"{previous}..{head}"], root)
    except RuntimeError as error:
        # Most often a force-push that removed the old commit. Reporting the
        # whole watched set as changed is the safe answer: it triggers a
        # re-derivation the reviewer can accept or discard, whereas silently
        # reporting nothing leaves the checks stale forever.
        logger.warning("Could not diff %s..%s: %s", previous, head, error)
        return RepoChange(
            from_commit=previous,
            to_commit=head,
            changed_paths=sorted(watched_paths(project)),
            subjects=[f"History rewritten or commit missing ({error})"],
        )

    changed = set(diff.split())
    watched = watched_paths(project)
    return RepoChange(
        from_commit=previous,
        to_commit=head,
        # Only the files this project's checks actually came from. A monorepo
        # commit touching an unrelated service is not a change to this
        # pipeline, and treating it as one would re-derive on every push.
        changed_paths=sorted(changed & watched),
        subjects=[s for s in log.splitlines() if s.strip()],
    )


def detect_warehouse_change(
    db: Session, project: models.Project
) -> tuple[list[WarehouseChange], list[str]]:
    """DDL observed in the warehouse in the recent past, per database."""
    changes: list[WarehouseChange] = []
    errors: list[str] = []

    for database in project.databases:
        connector_row = database.connector
        if connector_row is None:
            continue
        try:
            connector = build_connector(connector_row.type, connector_row.config)
        except Exception as error:  # noqa: BLE001
            errors.append(f"{database.name}: could not connect ({error})")
            continue

        try:
            type_list = ", ".join(f"'{t}'" for t in DDL_QUERY_TYPES)
            rows = connector.run_query(
                f"""SELECT QUERY_TYPE, START_TIME, USER_NAME, QUERY_TEXT
                    FROM TABLE({database.name}.INFORMATION_SCHEMA.QUERY_HISTORY(
                      END_TIME_RANGE_START => DATEADD('day', -{WAREHOUSE_LOOKBACK_DAYS}, CURRENT_TIMESTAMP())
                    ))
                    WHERE QUERY_TYPE IN ({type_list})
                      AND EXECUTION_STATUS = 'SUCCESS'
                    ORDER BY START_TIME DESC
                    LIMIT 50"""
            )
            for row in rows:
                changes.append(
                    WarehouseChange(
                        object_name=database.name,
                        query_type=str(row.get("QUERY_TYPE")),
                        executed_at=str(row.get("START_TIME")),
                        user_name=str(row.get("USER_NAME")),
                        statement=str(row.get("QUERY_TEXT"))[:1000],
                    )
                )
        except Exception as error:  # noqa: BLE001
            # A warehouse that will not answer must not stop the repo side.
            errors.append(f"{database.name}: could not read query history ({error})")
        finally:
            connector.close()

    return changes, errors


def _mentions_any(statement: str, subjects: list[str]) -> bool:
    """Whether a commit plausibly explains a DDL statement.

    Deliberately crude - it matches object names appearing in both - because
    the consequence of a false match is only that drift goes unreported this
    cycle, while a false *mismatch* cries wolf about every routine deploy.
    """
    haystack = " ".join(subjects).upper()
    tokens = {
        token.strip("(),;.").upper()
        for token in statement.split()
        if len(token) > 4 and token.replace("_", "").replace(".", "").isalnum()
    }
    return any(token in haystack for token in tokens)


def detect_changes(
    db: Session, project: models.Project, include_warehouse: bool = True
) -> ChangeReport:
    """Everything that moved for one project, from both signals."""
    report = ChangeReport(project_id=project.id, project_name=project.name)

    try:
        report.repo = detect_repo_change(project)
    except Exception as error:  # noqa: BLE001
        logger.warning("Repo change detection failed for %s: %s", project.name, error)
        report.errors.append(f"Repository: {error}")

    if include_warehouse:
        report.warehouse, warehouse_errors = detect_warehouse_change(db, project)
        report.errors.extend(warehouse_errors)

    # Drift: the warehouse changed and no commit in the range explains it.
    subjects = report.repo.subjects if report.repo else []
    if report.warehouse:
        report.undocumented = [
            change
            for change in report.warehouse
            if not _mentions_any(change.statement, subjects)
        ]

    return report
