"""Acting on a check defect: compile, repair, or stand down.

`defects.py` decides what an errored run was; this module does the parts that
need the warehouse and the database. Kept apart so the judgement stays pure and
testable, and so nothing here can quietly change what counts as a defect.

A repair is applied only after the repaired SQL has compiled against the live
warehouse (`EXPLAIN`, which plans a statement without reading data) - a repair
that is itself broken would just trade one invalid check for another. It is
applied without review only where the maintenance agent could already do so
(`Check.agent_may_rewrite`: derived, never edited by a person); anything else
gets the repair as a pending proposal instead, because overwriting a person's
edit is not ours to decide.
"""

import logging

from sqlalchemy.orm import Session

from app import models
from app.checks.defects import Assessment, assess_error, referenced_columns
from app.checks.sql import build_statements
from app.checks.stage import stage_for
from app.checks.versions import record_version
from app.connectors.base import Connector
from app.connectors.registry import build_connector
from app.models import cuid

logger = logging.getLogger(__name__)

# Only these statement shapes can be planned. A DESCRIBE (schema drift) has no
# plan, and is not generated from anything a MERGE said, so it is not at risk.
_EXPLAINABLE = ("SELECT", "WITH")


def compile_error(connector: Connector, check_type: str, config: dict) -> str | None:
    """None if every statement this check runs compiles, else the first error."""
    if check_type == "CROSS_SOURCE_PARITY":
        return None  # two systems; one EXPLAIN cannot speak for both
    try:
        statements = build_statements(check_type, config)
    except Exception as error:  # noqa: BLE001 - an unbuildable config is itself the answer
        return str(error)
    for statement in statements:
        if not statement.sql.lstrip().upper().startswith(_EXPLAINABLE):
            continue
        try:
            connector.run_query(f"EXPLAIN USING TEXT {statement.sql}")
        except Exception as error:  # noqa: BLE001 - the message is the result
            return str(error)
    return None


def live_columns(connector: Connector, tables: list[str]) -> dict[str, set[str] | None]:
    """Table -> its current columns, or None where it could not be described."""
    found: dict[str, set[str] | None] = {}
    for table in tables:
        try:
            schema = connector.get_schema(table)
            found[table.upper()] = {c["name"].upper() for c in schema["columns"]}
        except Exception:  # noqa: BLE001 - "could not tell" is a valid answer
            found[table.upper()] = None
    return found


def assess_run(check: models.Check, message: str | None) -> Assessment:
    """Classify an errored run of `check`, describing the tables it reads."""
    connector = build_connector(check.connector.type, check.connector.config)
    try:
        tables = list(referenced_columns(check.type, check.config or {}))
        assessment = assess_error(
            check.type, check.config or {}, message, live_columns(connector, tables)
        )
        if assessment.repaired_config is not None:
            error = compile_error(connector, check.type, assessment.repaired_config)
            if error:
                assessment.diagnostics["repair_compile_error"] = error[:2000]
                assessment.repaired_config = None
                assessment.summary = (
                    "Not monitored: the check's SQL was invalid and could not be repaired "
                    "automatically."
                )
        return assessment
    finally:
        connector.close()


def apply_repair(db: Session, check: models.Check, assessment: Assessment) -> bool:
    """Apply a compiled repair, or file it for review. True if applied now."""
    assert assessment.repaired_config is not None
    if not check.agent_may_rewrite:
        db.add(
            models.CheckRevision(
                id=cuid(),
                check_id=check.id,
                database_id=check.database_id,
                kind=models.RevisionKind.UPDATE.value,
                status=models.RevisionStatus.PENDING.value,
                current_config=check.config,
                proposed_config=assessment.repaired_config,
                reason=(
                    f"{assessment.repair_note} This check has been edited by a person, so "
                    "the fix is proposed rather than applied."
                ),
                confidence=1.0,
                detected_change={"kind": "check_defect", **assessment.diagnostics},
            )
        )
        return False

    check.config = assessment.repaired_config
    check.stage = stage_for(check.type, check.config, check.stage, check.stage_locked)
    record_version(db, check, author="agent", note=assessment.repair_note)
    logger.info("repaired check %s: %s", check.id, assessment.repair_note)
    return True
