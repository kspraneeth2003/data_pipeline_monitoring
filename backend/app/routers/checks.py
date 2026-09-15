from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session, joinedload

from app import models, schemas
from app.checks.config_schemas import CONFIG_SCHEMAS_BY_TYPE
from app.checks.runner import execute_check
from app.db import get_db
from app.models import cuid

router = APIRouter(prefix="/api/checks", tags=["checks"])


def _query_with_relations(db: Session):
    return db.query(models.Check).options(
        joinedload(models.Check.connector),
        joinedload(models.Check.secondary_connector),
        joinedload(models.Check.runs).joinedload(models.CheckRun.rca),
        joinedload(models.Check.runs).joinedload(models.CheckRun.ticket),
    )


def _validate_config(check_type: str, config: dict) -> dict:
    schema = CONFIG_SCHEMAS_BY_TYPE.get(check_type)
    if not schema:
        raise HTTPException(400, f"Unknown check type: {check_type}")
    try:
        return schema.model_validate(config).model_dump(exclude_none=True)
    except ValidationError as error:
        raise HTTPException(400, error.errors()) from error


@router.get("", response_model=list[schemas.CheckOut])
def list_checks(db: Session = Depends(get_db)):
    checks = _query_with_relations(db).order_by(models.Check.created_at.asc()).all()
    for check in checks:
        check.runs = sorted(check.runs, key=lambda r: r.started_at, reverse=True)[:1]
    return checks


@router.get("/{check_id}", response_model=schemas.CheckOut)
def get_check(check_id: str, db: Session = Depends(get_db)):
    check = _query_with_relations(db).filter(models.Check.id == check_id).first()
    if not check:
        raise HTTPException(404, "Check not found")
    check.runs = sorted(check.runs, key=lambda r: r.started_at, reverse=True)[:25]
    return check


@router.post("", response_model=schemas.CheckOut, status_code=201)
def create_check(payload: schemas.CheckCreate, db: Session = Depends(get_db)):
    if not croniter.is_valid(payload.schedule):
        raise HTTPException(400, f"Invalid cron expression: {payload.schedule}")
    if payload.type == "CROSS_SOURCE_PARITY" and not payload.secondary_connector_id:
        raise HTTPException(400, "CROSS_SOURCE_PARITY checks require a secondary_connector_id")

    if not db.query(models.Project).filter_by(id=payload.project_id).first():
        raise HTTPException(400, "project_id does not match an existing project")

    config = _validate_config(payload.type, payload.config)

    check = models.Check(
        id=cuid(),
        name=payload.name,
        description=payload.description,
        type=payload.type,
        schedule=payload.schedule,
        enabled=payload.enabled,
        project_id=payload.project_id,
        connector_id=payload.connector_id,
        secondary_connector_id=payload.secondary_connector_id,
        config=config,
    )
    db.add(check)
    db.commit()
    return _query_with_relations(db).filter(models.Check.id == check.id).first()


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

    for field in ["name", "description", "type", "schedule", "enabled", "connector_id", "secondary_connector_id"]:
        value = getattr(payload, field)
        if value is not None:
            setattr(check, field, value)

    db.commit()
    return _query_with_relations(db).filter(models.Check.id == check_id).first()


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
