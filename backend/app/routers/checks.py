from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session, joinedload

from app import models, schemas
from app.checks.config_schemas import CONFIG_SCHEMAS_BY_TYPE
from app.checks.runner import execute_check
from app.checks.sql import try_build_statements
from app.db import get_db
from app.monitoring.incidents import utcnow
from app.models import cuid

router = APIRouter(prefix="/api/checks", tags=["checks"])


def _query_with_relations(db: Session):
    return db.query(models.Check).options(
        joinedload(models.Check.connector),
        joinedload(models.Check.secondary_connector),
        joinedload(models.Check.database).joinedload(models.Database.project),
        joinedload(models.Check.runs).joinedload(models.CheckRun.rca),
        # There is deliberately no ticket eager-load here. A `Ticket` used to
        # hang off a CheckRun; tickets now live in Jira and are reached through
        # an Incident instead. This line outlived the relationship it named and
        # raised AttributeError on every route in this file - see the test that
        # now pins it.
    )


def _validate_config(check_type: str, config: dict) -> dict:
    schema = CONFIG_SCHEMAS_BY_TYPE.get(check_type)
    if not schema:
        raise HTTPException(400, f"Unknown check type: {check_type}")
    try:
        return schema.model_validate(config).model_dump(exclude_none=True)
    except ValidationError as error:
        raise HTTPException(400, error.errors()) from error


def _with_statements(check: models.Check | None) -> models.Check | None:
    """Attach the SQL this check would run, for the response.

    Built on read rather than stored, so what a reviewer sees is what the
    engine would execute - a stored copy would be a second source of truth and
    would go stale the first time a builder changed. `try_` rather than
    `build_` because one unbuildable check must not blank a list view; the
    error travels with that check as the finding it is.
    """
    if check is None:
        return None
    statements, error = try_build_statements(check.type, check.config)
    check.statements = statements
    check.statements_error = error
    return check


@router.get("", response_model=list[schemas.CheckOut])
def list_checks(db: Session = Depends(get_db)):
    checks = _query_with_relations(db).order_by(models.Check.created_at.asc()).all()
    for check in checks:
        check.runs = sorted(check.runs, key=lambda r: r.started_at, reverse=True)[:1]
        _with_statements(check)
    return checks


@router.get("/{check_id}", response_model=schemas.CheckOut)
def get_check(check_id: str, db: Session = Depends(get_db)):
    check = _query_with_relations(db).filter(models.Check.id == check_id).first()
    if not check:
        raise HTTPException(404, "Check not found")
    check.runs = sorted(check.runs, key=lambda r: r.started_at, reverse=True)[:25]
    return _with_statements(check)


@router.post("", response_model=schemas.CheckOut, status_code=201)
def create_check(payload: schemas.CheckCreate, db: Session = Depends(get_db)):
    if not croniter.is_valid(payload.schedule):
        raise HTTPException(400, f"Invalid cron expression: {payload.schedule}")
    if payload.type == "CROSS_SOURCE_PARITY" and not payload.secondary_connector_id:
        raise HTTPException(400, "CROSS_SOURCE_PARITY checks require a secondary_connector_id")

    database = db.query(models.Database).filter_by(id=payload.database_id).first()
    if not database:
        raise HTTPException(400, "database_id does not match an existing database")

    config = _validate_config(payload.type, payload.config)

    check = models.Check(
        id=cuid(),
        name=payload.name,
        description=payload.description,
        rationale=payload.rationale,
        type=payload.type,
        schedule=payload.schedule,
        enabled=payload.enabled,
        database_id=payload.database_id,
        connector_id=payload.connector_id,
        secondary_connector_id=payload.secondary_connector_id,
        config=config,
    )
    db.add(check)
    db.commit()
    return _with_statements(_query_with_relations(db).filter(models.Check.id == check.id).first())


@router.patch("/{check_id}", response_model=schemas.CheckOut)
def update_check(check_id: str, payload: schemas.CheckUpdate, db: Session = Depends(get_db)):
    check = db.query(models.Check).filter_by(id=check_id).first()
    if not check:
        raise HTTPException(404, "Check not found")

    if payload.schedule and not croniter.is_valid(payload.schedule):
        raise HTTPException(400, f"Invalid cron expression: {payload.schedule}")

    effective_type = payload.type or check.type
    if payload.config is not None:
        check.config = _validate_config(effective_type, payload.config)

    for field in [
        "name",
        "description",
        "rationale",
        "type",
        "schedule",
        "enabled",
        "connector_id",
        "secondary_connector_id",
    ]:
        value = getattr(payload, field)
        if value is not None:
            setattr(check, field, value)

    # A person has now expressed intent about this check, so the maintenance
    # agent may no longer rewrite it without review. Stamped on any edit
    # rather than only on a config change: renaming a check or moving its
    # schedule is still someone deciding it should be this way.
    check.human_edited_at = utcnow()

    db.commit()
    return _with_statements(_query_with_relations(db).filter(models.Check.id == check_id).first())


@router.delete("/{check_id}")
def delete_check(check_id: str, db: Session = Depends(get_db)):
    check = db.query(models.Check).filter_by(id=check_id).first()
    if not check:
        raise HTTPException(404, "Check not found")
    db.delete(check)
    db.commit()
    return {"ok": True}


@router.post("/{check_id}/run")
def run_check_now(check_id: str, db: Session = Depends(get_db)):
    try:
        run_id = execute_check(db, check_id)
    except Exception as error:
        raise HTTPException(500, str(error)) from error
    return {"run_id": run_id}
