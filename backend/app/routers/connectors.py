from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models, schemas
from app.connectors.registry import build_connector
from app.connectors.security import encrypt_config_secrets, redact_config_secrets
from app.connectors.snowflake_connector import probe_snowflake, test_snowflake_connection
from app.db import get_db
from app.models import cuid

router = APIRouter(prefix="/api/connectors", tags=["connectors"])


def _to_out(connector: models.Connector, checks_count: int = 0) -> schemas.ConnectorOut:
    data = schemas.ConnectorOut.model_validate(connector).model_dump()
    data["config"] = redact_config_secrets(connector.type, connector.config)
    data["checks_count"] = checks_count
    return schemas.ConnectorOut.model_validate(data)


@router.post("/probe", response_model=schemas.ConnectionProbeResult)
def probe_connection(payload: schemas.ConnectionProbe):
    """Test credentials and list reachable databases without saving anything."""
    if payload.type != "SNOWFLAKE":
        raise HTTPException(400, f"Cannot probe connector type {payload.type}")
    if not payload.config.get("password") and not payload.config.get("privateKey"):
        raise HTTPException(400, "Provide either a password or a private key")
    try:
        return schemas.ConnectionProbeResult(**probe_snowflake(payload.config))
    except HTTPException:
        raise
    except Exception as error:  # noqa: BLE001 - surfaced to the user as a 400
        raise HTTPException(400, f"Could not connect: {error}") from error


@router.get("", response_model=list[schemas.ConnectorOut])
def list_connectors(project_id: str | None = None, db: Session = Depends(get_db)):
    query = (
        db.query(models.Connector, func.count(models.Check.id))
        .outerjoin(models.Check, models.Check.connector_id == models.Connector.id)
    )
    if project_id:
        query = query.filter(models.Connector.project_id == project_id)
    rows = query.group_by(models.Connector.id).order_by(models.Connector.created_at.asc()).all()
    return [_to_out(connector, count) for connector, count in rows]


@router.post("", response_model=schemas.ConnectorOut, status_code=201)
def create_connector(payload: schemas.ConnectorCreate, db: Session = Depends(get_db)):
    if not payload.project_id or not db.query(models.Project).filter_by(id=payload.project_id).first():
        raise HTTPException(400, "project_id does not match an existing project")
    if payload.type == "SNOWFLAKE":
        if not payload.config.get("password") and not payload.config.get("privateKey"):
            raise HTTPException(400, "Provide either a password or a private key")
        if payload.test_connection:
            try:
                test_snowflake_connection(payload.config)
            except Exception as error:
                raise HTTPException(400, f"Connection test failed: {error}") from error

    connector = models.Connector(
        id=cuid(),
        project_id=payload.project_id,
        name=payload.name,
        type=payload.type,
        comment=payload.comment,
        config=encrypt_config_secrets(payload.type, payload.config),
    )
    db.add(connector)
    db.commit()
    db.refresh(connector)
    return _to_out(connector)


@router.patch("/{connector_id}", response_model=schemas.ConnectorOut)
def update_connector(connector_id: str, payload: schemas.ConnectorUpdate, db: Session = Depends(get_db)):
    connector = db.query(models.Connector).filter_by(id=connector_id).first()
    if not connector:
        raise HTTPException(404, "Connector not found")

    merged_config = {**connector.config, **payload.config} if payload.config else connector.config
    encrypted_config = encrypt_config_secrets(connector.type, merged_config) if payload.config else merged_config

    if payload.config and payload.test_connection and connector.type == "SNOWFLAKE":
        try:
            test_snowflake_connection(encrypted_config)
        except Exception as error:
            raise HTTPException(400, f"Connection test failed: {error}") from error

    if payload.name is not None:
        connector.name = payload.name
    if payload.comment is not None:
        connector.comment = payload.comment
    connector.config = encrypted_config

    db.commit()
    db.refresh(connector)
    return _to_out(connector)


@router.delete("/{connector_id}")
def delete_connector(connector_id: str, db: Session = Depends(get_db)):
    used_by = (
        db.query(models.Check)
        .filter((models.Check.connector_id == connector_id) | (models.Check.secondary_connector_id == connector_id))
        .count()
    )
    if used_by > 0:
        raise HTTPException(409, f"Cannot delete connector: {used_by} check(s) still reference it")

    connector = db.query(models.Connector).filter_by(id=connector_id).first()
    if not connector:
        raise HTTPException(404, "Connector not found")
    db.delete(connector)
    db.commit()
    return {"ok": True}


@router.get("/{connector_id}/databases", response_model=list[str])
def discover_databases(connector_id: str, db: Session = Depends(get_db)):
    """Databases this connector can actually see.

    Adding a database to a project should be a choice from what exists, not a
    free-text field where a typo becomes a check that errors at its first run.
    """
    connector = db.query(models.Connector).filter_by(id=connector_id).first()
    if not connector:
        raise HTTPException(404, "Connector not found")

    handle = build_connector(connector.type, connector.config)
    try:
        rows = handle.run_query("SHOW DATABASES")
    except Exception as error:  # noqa: BLE001 - surfaced to the user as a 400
        raise HTTPException(400, f"Could not list databases: {error}") from error
    finally:
        handle.close()

    # SHOW DATABASES returns a wide row; the database name is under "name".
    names = [str(row.get("name")) for row in rows if row.get("name")]
    return sorted(n for n in names if n not in {"SNOWFLAKE", "SNOWFLAKE_SAMPLE_DATA"})
