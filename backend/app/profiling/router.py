"""The profiling API: what is profiled, what it found, and what the data looks like."""

from datetime import datetime, timezone

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import models
from app.db import get_db
from app.profiling import detector
from app.profiling.models import (
    ColumnProfile,
    DatabaseCatalogSync,
    ProfileAnomaly,
    ProfileRun,
    ProfileRunStatus,
    ProfileTarget,
)
from app.profiling.scheduler import queue_now
from app.profiling.service import (
    explain_warehouse_error,
    list_tables,
    run_profile,
    sync_database_catalog,
)
from app.profiling.sql import parse_object

router = APIRouter(prefix="/api", tags=["profiling"])


# --- Schemas ---------------------------------------------------------------


class TargetCreate(BaseModel):
    database_id: str
    object: str
    schedule: str = "0 * * * *"


class TargetUpdate(BaseModel):
    enabled: bool | None = None
    schedule: str | None = None


class DiscoverRequest(BaseModel):
    database_id: str
    # Limit to one schema; omitted means the whole database.
    schema_name: str | None = None


class RunOut(BaseModel):
    id: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    row_count: int | None
    column_count: int | None
    anomaly_count: int
    message: str | None


class AnomalyOut(BaseModel):
    id: str
    target_id: str
    object: str
    database_slug: str
    run_id: str
    column_name: str | None
    kind: str
    metric: str
    severity: str
    observed: float | None
    expected: float | None
    lower: float | None
    upper: float | None
    message: str
    acknowledged_at: datetime | None
    created_at: datetime


class TargetOut(BaseModel):
    id: str
    object: str
    schedule: str
    enabled: bool
    database: dict
    last_run: RunOut | None
    successful_runs: int
    baseline_runs_needed: int
    open_anomalies: int


class ColumnOut(BaseModel):
    column_name: str
    data_type: str
    family: str
    ordinal: int
    row_count: int
    null_count: int
    null_ratio: float | None
    distinct_count: int | None
    blank_count: int | None
    min_value: str | None
    max_value: str | None
    # The display strings above are formatted per family (commas, "N chars",
    # a timestamp) so a column-header sort can't compare them as numbers
    # correctly. These are the raw values behind them - a number, epoch
    # seconds for a timestamp, or a length for text - which is what a sort
    # actually needs.
    min_numeric: float | None
    max_numeric: float | None
    mean_numeric: float | None


class HistoryPoint(BaseModel):
    run_id: str
    at: datetime
    row_count: int | None
    # column name -> metric -> value
    columns: dict[str, dict[str, float | None]]


class TargetDetailOut(TargetOut):
    columns: list[ColumnOut]
    history: list[HistoryPoint]
    anomalies: list[AnomalyOut]


class CatalogTable(BaseModel):
    object: str
    table: str
    row_count: int | None
    last_altered: datetime | None
    target: TargetOut | None


class CatalogSchema(BaseModel):
    name: str
    tables: list[CatalogTable]


class CatalogDatabase(BaseModel):
    id: str
    name: str
    slug: str
    readable: bool
    error: str | None
    schemas: list[CatalogSchema]
    synced_at: datetime | None


class SyncResult(BaseModel):
    database: str
    readable: bool
    tables: int
    new_tables_queued: int


class SyncOut(BaseModel):
    synced_at: datetime
    databases: list[SyncResult]


# --- Helpers ---------------------------------------------------------------


def _project_or_404(db: Session, slug: str) -> models.Project:
    project = db.scalars(select(models.Project).where(models.Project.slug == slug)).first()
    if project is None:
        raise HTTPException(404, "Project not found")
    return project


def _target_or_404(db: Session, target_id: str) -> ProfileTarget:
    target = db.get(ProfileTarget, target_id)
    if target is None:
        raise HTTPException(404, "Profile target not found")
    return target


def _validate_cron(schedule: str) -> None:
    try:
        CronTrigger.from_crontab(schedule)
    except ValueError as exc:
        raise HTTPException(422, f"Invalid cron schedule {schedule!r}: {exc}") from exc


def _run_out(run: ProfileRun | None) -> RunOut | None:
    if run is None:
        return None
    return RunOut(
        id=run.id, status=run.status, started_at=run.started_at, finished_at=run.finished_at,
        duration_ms=run.duration_ms, row_count=run.row_count, column_count=run.column_count,
        anomaly_count=run.anomaly_count, message=run.message,
    )


def _latest_run(db: Session, target_id: str, *, successful: bool = False) -> ProfileRun | None:
    query = select(ProfileRun).where(ProfileRun.target_id == target_id)
    if successful:
        query = query.where(ProfileRun.status == ProfileRunStatus.SUCCEEDED.value)
    return db.scalars(query.order_by(ProfileRun.started_at.desc()).limit(1)).first()


def _anomaly_out(a: ProfileAnomaly, target: ProfileTarget) -> AnomalyOut:
    return AnomalyOut(
        id=a.id, target_id=a.target_id, object=target.object, database_slug=target.database.slug,
        run_id=a.run_id, column_name=a.column_name, kind=a.kind, metric=a.metric, severity=a.severity,
        observed=a.observed, expected=a.expected, lower=a.lower, upper=a.upper, message=a.message,
        acknowledged_at=a.acknowledged_at, created_at=a.created_at,
    )


def _active_anomalies(db: Session, target: ProfileTarget, include_acknowledged: bool = False) -> list[ProfileAnomaly]:
    """Anomalies on the target's latest successful run - the ones still true now."""
    latest = _latest_run(db, target.id, successful=True)
    if latest is None:
        return []
    query = select(ProfileAnomaly).where(ProfileAnomaly.run_id == latest.id)
    if not include_acknowledged:
        query = query.where(ProfileAnomaly.acknowledged_at.is_(None))
    return list(db.scalars(query).all())


def _target_out(db: Session, target: ProfileTarget) -> TargetOut:
    successful = db.scalar(
        select(func.count()).select_from(ProfileRun).where(
            ProfileRun.target_id == target.id, ProfileRun.status == ProfileRunStatus.SUCCEEDED.value
        )
    ) or 0
    return TargetOut(
        id=target.id,
        object=target.object,
        schedule=target.schedule,
        enabled=target.enabled,
        database={"id": target.database.id, "name": target.database.name, "slug": target.database.slug},
        last_run=_run_out(_latest_run(db, target.id)),
        successful_runs=successful,
        # The baseline is prior runs, so a run needs MIN_HISTORY before it.
        baseline_runs_needed=max(0, detector.MIN_HISTORY + 1 - successful),
        open_anomalies=len(_active_anomalies(db, target)),
    )


def _project_targets(db: Session, project: models.Project) -> list[ProfileTarget]:
    database_ids = [d.id for d in project.databases]
    if not database_ids:
        return []
    return list(db.scalars(
        select(ProfileTarget).where(ProfileTarget.database_id.in_(database_ids)).order_by(ProfileTarget.object)
    ).all())


def _provision_new_targets(db: Session, database: models.Database, tables: list[dict]) -> int:
    """Track and run, right now, every table this database holds that nothing here knows about yet.

    This is what makes a first visit self-sufficient: without it, a newly
    discovered table would sit in the catalog with a "Profile" action nobody
    has clicked, rather than already having a result on screen.
    """
    existing = set(db.scalars(select(ProfileTarget.object).where(ProfileTarget.database_id == database.id)).all())
    added = [t["object"] for t in tables if t["object"] not in existing]
    if not added:
        return 0
    new_targets = [ProfileTarget(database_id=database.id, object=obj) for obj in added]
    db.add_all(new_targets)
    db.flush()
    return queue_now([t.id for t in new_targets])


# --- Routes ----------------------------------------------------------------


@router.get("/projects/{slug}/profiling", response_model=list[TargetOut])
def list_targets(slug: str, db: Session = Depends(get_db)):
    project = _project_or_404(db, slug)
    return [_target_out(db, t) for t in _project_targets(db, project)]


@router.post("/projects/{slug}/profiling/targets", response_model=TargetOut, status_code=201)
def create_target(slug: str, payload: TargetCreate, db: Session = Depends(get_db)):
    project = _project_or_404(db, slug)
    database = next((d for d in project.databases if d.id == payload.database_id), None)
    if database is None:
        raise HTTPException(404, "Database not found in this project")
    name = payload.object.strip()
    # SCHEMA.TABLE is accepted and qualified with the database it was added to.
    if name.count(".") == 1:
        name = f"{database.name}.{name}"
    try:
        ref = parse_object(name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if ref.database != database.name.upper():
        raise HTTPException(422, f"{ref.qualified} is not in database {database.name}")
    _validate_cron(payload.schedule)
    exists = db.scalars(
        select(ProfileTarget).where(ProfileTarget.database_id == database.id, ProfileTarget.object == ref.qualified)
    ).first()
    if exists:
        raise HTTPException(409, f"{ref.qualified} is already profiled")
    target = ProfileTarget(database_id=database.id, object=ref.qualified, schedule=payload.schedule)
    db.add(target)
    db.commit()
    db.refresh(target)
    return _target_out(db, target)


@router.post("/projects/{slug}/profiling/discover")
def discover_targets(slug: str, payload: DiscoverRequest, db: Session = Depends(get_db)):
    """Profile every base table in a database. Tables already profiled are left alone."""
    project = _project_or_404(db, slug)
    database = next((d for d in project.databases if d.id == payload.database_id), None)
    if database is None:
        raise HTTPException(404, "Database not found in this project")
    try:
        tables = list_tables(database.connector.type, database.connector.config, database.name)
    except Exception as exc:  # noqa: BLE001 - surface the warehouse's reason, not a 500
        raise HTTPException(502, explain_warehouse_error(exc, database.name)) from exc
    if payload.schema_name:
        prefix = f"{database.name.upper()}.{payload.schema_name.upper()}."
        tables = [t for t in tables if t.startswith(prefix)]
    existing = set(db.scalars(select(ProfileTarget.object).where(ProfileTarget.database_id == database.id)).all())
    added = [t for t in tables if t not in existing]
    new_targets = [ProfileTarget(database_id=database.id, object=name) for name in added]
    db.add_all(new_targets)
    db.commit()
    # Run them now rather than at the next top of the hour.
    queued = queue_now([t.id for t in new_targets])
    return {"added": added, "already_profiled": sorted(existing & set(tables)), "tables": tables, "queued": queued}


@router.post("/projects/{slug}/profiling/run-all")
def run_all(slug: str, payload: DiscoverRequest, db: Session = Depends(get_db)):
    """Re-profile every enabled table in a database now, in the background."""
    project = _project_or_404(db, slug)
    database = next((d for d in project.databases if d.id == payload.database_id), None)
    if database is None:
        raise HTTPException(404, "Database not found in this project")
    ids = list(db.scalars(
        select(ProfileTarget.id).where(ProfileTarget.database_id == database.id, ProfileTarget.enabled.is_(True))
    ).all())
    return {"queued": queue_now(ids), "targets": len(ids)}


@router.post("/projects/{slug}/profiling/sync", response_model=SyncOut)
def sync_catalog(slug: str, db: Session = Depends(get_db)):
    """Refresh every database's table list from Snowflake, right now.

    `GET .../catalog` reads a cache so an ordinary page view costs one
    Postgres query, not a Snowflake session per database - this is the
    button that pays for a live read on purpose. Any table found for the
    first time is tracked and profiled immediately, the same as it would be
    on a brand new project's very first catalog request; a table this
    already knew about is left on its own schedule, since re-running
    everything on every sync would make sync itself the slow thing it
    replaced.
    """
    project = _project_or_404(db, slug)
    results: list[SyncResult] = []
    for database in project.databases:
        tables, error = sync_database_catalog(db, database)
        queued = _provision_new_targets(db, database, tables) if error is None else 0
        results.append(SyncResult(database=database.name, readable=error is None, tables=len(tables), new_tables_queued=queued))
    db.commit()
    return SyncOut(synced_at=datetime.now(timezone.utc).replace(tzinfo=None), databases=results)


@router.get("/projects/{slug}/profiling/catalog", response_model=list[CatalogDatabase])
def catalog(slug: str, db: Session = Depends(get_db)):
    """Every database, schema and table in the project, and which are profiled.

    Reads the cached table list (`DatabaseCatalogSync`), not Snowflake -
    `POST .../profiling/sync` is what refreshes that cache, on request rather
    than on every page view. The one exception is a database whose catalog
    has never been synced at all: that read happens here, once, inline, and
    every table it finds is tracked and profiled immediately - so a project
    nobody has opened Profiling for yet still ends up with results the first
    time someone does, rather than a page of "Profile" buttons.

    A database the connection cannot read is still listed, with the reason,
    rather than silently left out: an absent database reads as "nothing to
    profile", which is the wrong conclusion.
    """
    project = _project_or_404(db, slug)

    # First-ever view of a database's catalog: sync and provision inline.
    for database in project.databases:
        cached = db.scalars(select(DatabaseCatalogSync).where(DatabaseCatalogSync.database_id == database.id)).first()
        if cached is None:
            tables, error = sync_database_catalog(db, database)
            if error is None:
                _provision_new_targets(db, database, tables)
    db.commit()

    targets = {(t.database_id, t.object): t for t in _project_targets(db, project)}
    caches = {
        c.database_id: c
        for c in db.scalars(
            select(DatabaseCatalogSync).where(DatabaseCatalogSync.database_id.in_([d.id for d in project.databases]))
        ).all()
    }
    out: list[CatalogDatabase] = []
    for database in project.databases:
        cached = caches.get(database.id)
        tables = cached.tables if cached and cached.readable else []
        error = cached.error if cached else None
        schemas: dict[str, list[CatalogTable]] = {}
        seen: set[str] = set()
        for t in tables:
            target = targets.get((database.id, t["object"]))
            seen.add(t["object"])
            schemas.setdefault(t["schema"], []).append(CatalogTable(
                object=t["object"], table=t["table"], row_count=t["row_count"],
                last_altered=t["last_altered"], target=_target_out(db, target) if target else None,
            ))
        # A profiled table the warehouse no longer lists is kept visible, so
        # its history and last error are still reachable.
        for (db_id, obj), target in targets.items():
            if db_id == database.id and obj not in seen and not error:
                schema_name, table_name = obj.split(".")[1:3]
                schemas.setdefault(schema_name, []).append(CatalogTable(
                    object=obj, table=table_name, row_count=None, last_altered=None, target=_target_out(db, target),
                ))
        out.append(CatalogDatabase(
            id=database.id, name=database.name, slug=database.slug, readable=error is None, error=error,
            schemas=[CatalogSchema(name=s, tables=v) for s, v in sorted(schemas.items())],
            synced_at=cached.synced_at if cached else None,
        ))
    return out


@router.get("/profiling/targets/{target_id}", response_model=TargetDetailOut)
def get_target(target_id: str, db: Session = Depends(get_db)):
    target = _target_or_404(db, target_id)
    base = _target_out(db, target)

    latest = _latest_run(db, target.id, successful=True)
    columns: list[ColumnOut] = []
    if latest is not None:
        for c in db.scalars(
            select(ColumnProfile).where(ColumnProfile.run_id == latest.id).order_by(ColumnProfile.ordinal)
        ).all():
            columns.append(ColumnOut(
                column_name=c.column_name, data_type=c.data_type, family=c.family, ordinal=c.ordinal,
                row_count=c.row_count, null_count=c.null_count,
                null_ratio=(c.null_count / c.row_count) if c.row_count else None,
                distinct_count=c.distinct_count, blank_count=c.blank_count,
                min_value=c.min_value, max_value=c.max_value,
                min_numeric=c.min_numeric, max_numeric=c.max_numeric, mean_numeric=c.mean_numeric,
            ))

    runs = list(reversed(db.scalars(
        select(ProfileRun)
        .where(ProfileRun.target_id == target.id, ProfileRun.status == ProfileRunStatus.SUCCEEDED.value)
        .options(selectinload(ProfileRun.columns))
        .order_by(ProfileRun.started_at.desc())
        .limit(detector.HISTORY_WINDOW)
    ).all()))
    history = [
        HistoryPoint(
            run_id=r.id,
            at=r.finished_at or r.started_at,
            row_count=r.row_count,
            columns={
                c.column_name: {
                    "null_ratio": (c.null_count / c.row_count) if c.row_count else None,
                    "distinct_count": c.distinct_count,
                    "mean": c.mean_numeric,
                }
                for c in r.columns
            },
        )
        for r in runs
    ]

    anomalies = db.scalars(
        select(ProfileAnomaly)
        .where(ProfileAnomaly.target_id == target.id)
        .order_by(ProfileAnomaly.created_at.desc())
        .limit(100)
    ).all()
    return TargetDetailOut(
        **base.model_dump(),
        columns=columns,
        history=history,
        anomalies=[_anomaly_out(a, target) for a in anomalies],
    )


@router.patch("/profiling/targets/{target_id}", response_model=TargetOut)
def update_target(target_id: str, payload: TargetUpdate, db: Session = Depends(get_db)):
    target = _target_or_404(db, target_id)
    if payload.schedule is not None:
        _validate_cron(payload.schedule)
        target.schedule = payload.schedule
    if payload.enabled is not None:
        target.enabled = payload.enabled
    db.commit()
    db.refresh(target)
    return _target_out(db, target)


@router.delete("/profiling/targets/{target_id}", status_code=204)
def delete_target(target_id: str, db: Session = Depends(get_db)):
    db.delete(_target_or_404(db, target_id))
    db.commit()


@router.post("/profiling/targets/{target_id}/run", response_model=RunOut)
def run_target(target_id: str, db: Session = Depends(get_db)):
    _target_or_404(db, target_id)
    return _run_out(run_profile(db, target_id))


@router.get("/projects/{slug}/anomalies", response_model=list[AnomalyOut])
def list_anomalies(slug: str, include_acknowledged: bool = False, db: Session = Depends(get_db)):
    """What is true right now: anomalies on each table's latest successful profile."""
    project = _project_or_404(db, slug)
    out: list[AnomalyOut] = []
    for target in _project_targets(db, project):
        out += [_anomaly_out(a, target) for a in _active_anomalies(db, target, include_acknowledged)]
    rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    return sorted(out, key=lambda a: (rank.get(a.severity, 3), a.object, a.column_name or ""))


@router.post("/profiling/anomalies/{anomaly_id}/acknowledge", response_model=AnomalyOut)
def acknowledge(anomaly_id: str, db: Session = Depends(get_db)):
    anomaly = db.get(ProfileAnomaly, anomaly_id)
    if anomaly is None:
        raise HTTPException(404, "Anomaly not found")
    anomaly.acknowledged_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    db.refresh(anomaly)
    return _anomaly_out(anomaly, db.get(ProfileTarget, anomaly.target_id))
