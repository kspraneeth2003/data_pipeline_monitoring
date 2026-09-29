"""Recording what a check's logic was, each time it changes.

One entry point, `record_version`, called from every path that writes a check.
Centralised rather than inlined at each call site so that "a config change
always produces a rendered SQL snapshot" is a property of one function instead
of a convention four routers have to remember.
"""

from sqlalchemy.orm import Session

from app import models
from app.checks.sql import try_build_statements
from app.models import cuid


def render(check_type: str, config: dict) -> tuple[list[dict], str | None, str | None]:
    """The statements a config renders to, as storable JSON plus flat text.

    Returns `(statements, sql_text, error)`. On failure the first two are empty
    and the error travels instead - the same contract as `try_build_statements`,
    and for the same reason: a version that would not have run is still a
    version somebody needs to be able to look at.
    """
    built, error = try_build_statements(check_type, config)
    if error:
        return [], None, error

    statements = [{"label": s.label, "sql": s.sql, "connection": s.connection} for s in built]
    # Labels are carried into the text so a multi-statement check reads as one
    # document. Without them a parity snapshot is three anonymous queries and
    # the diff between two versions says nothing about which side moved.
    sql_text = "\n\n".join(f"-- {s['label']}\n{s['sql']}" for s in statements)
    return statements, sql_text, None


def _is_unchanged(latest: models.CheckVersion | None, check: models.Check) -> bool:
    """Whether the check already has this exact logic as its latest version.

    Guards against a PATCH that touched only `enabled` or a description writing
    a version identical to the one before it. A history padded with no-op
    entries is one nobody scrolls, which costs more than the rows do.
    """
    if latest is None:
        return False
    return (
        latest.name == check.name
        and latest.type == check.type
        and latest.schedule == check.schedule
        and latest.config == check.config
    )


def record_version(
    db: Session,
    check: models.Check,
    *,
    author: str = "human",
    note: str | None = None,
    force: bool = False,
) -> models.CheckVersion | None:
    """Append a version for `check` as it currently stands, and render its SQL.

    Returns the new version, or None when the logic was unchanged and `force`
    was not set. Does not commit - the caller owns the transaction, so a failed
    write cannot leave a version recording a change that never landed.
    """
    latest = (
        db.query(models.CheckVersion)
        .filter_by(check_id=check.id)
        .order_by(models.CheckVersion.version.desc())
        .first()
    )
    if not force and _is_unchanged(latest, check):
        return None

    statements, sql_text, error = render(check.type, check.config or {})
    version = models.CheckVersion(
        id=cuid(),
        check_id=check.id,
        version=(latest.version + 1) if latest else 1,
        name=check.name,
        type=check.type,
        schedule=check.schedule,
        config=check.config or {},
        statements=statements,
        sql_text=sql_text,
        statements_error=error,
        author=author,
        note=note,
    )
    db.add(version)
    return version
