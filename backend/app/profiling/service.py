"""Running a profile: read the columns, one aggregate statement, store, judge.

Every statement issued here is a SELECT - against `INFORMATION_SCHEMA` or the
table itself. The profile never writes to the warehouse.
"""

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.connectors.registry import build_connector
from app.models import cuid
from app.profiling import detector
from app.profiling.models import (
    AnomalyKind,
    ColumnProfile,
    DatabaseCatalogSync,
    ProfileAnomaly,
    ProfileRun,
    ProfileRunStatus,
    ProfileTarget,
)
from app.profiling.sql import (
    NUMERIC,
    TEMPORAL,
    TEXT,
    ColumnSpec,
    aggregate_aliases,
    columns_sql,
    parse_object,
    profile_sql,
    tables_sql,
)

logger = logging.getLogger("dpm.profiling")


def _utcnow() -> datetime:
    # Naive UTC, matching every other timestamp this app stores.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _int(value) -> int | None:
    return None if value is None else int(value)


def _float(value) -> float | None:
    return None if value is None else float(value)


def _display(value: float | None, family: str) -> str | None:
    if value is None:
        return None
    if family == TEXT:
        return f"{value:.0f} chars"
    if family == NUMERIC:
        return f"{value:,.0f}" if value == int(value) else f"{value:,.4g}"
    return None


def _to_metrics(profile: ColumnProfile) -> detector.ColumnMetrics:
    return detector.ColumnMetrics(
        name=profile.column_name,
        data_type=profile.data_type,
        family=profile.family,
        row_count=profile.row_count,
        null_count=profile.null_count,
        distinct_count=profile.distinct_count,
        blank_count=profile.blank_count,
        min_numeric=profile.min_numeric,
        max_numeric=profile.max_numeric,
        mean_numeric=profile.mean_numeric,
    )


def snapshot_of(run: ProfileRun) -> detector.Snapshot:
    return detector.Snapshot(
        row_count=run.row_count or 0,
        columns={c.column_name: _to_metrics(c) for c in run.columns},
    )


def prior_runs(db: Session, target_id: str, before: ProfileRun, limit: int = detector.HISTORY_WINDOW) -> list[ProfileRun]:
    """Successful runs before `before`, oldest first - the baseline it is judged against."""
    runs = db.scalars(
        select(ProfileRun)
        .where(
            ProfileRun.target_id == target_id,
            ProfileRun.status == ProfileRunStatus.SUCCEEDED.value,
            ProfileRun.id != before.id,
            ProfileRun.started_at <= before.started_at,
        )
        .options(selectinload(ProfileRun.columns))
        .order_by(ProfileRun.started_at.desc())
        .limit(limit)
    ).all()
    return list(reversed(runs))


def _acknowledged_keys(db: Session, target_id: str) -> set[tuple[str | None, str]]:
    """Findings someone has already said are expected, so they stay quiet.

    Only findings carry forward. An anomaly is about *this* run differing
    from the past, and acknowledging last Tuesday's spike must not silence
    next Tuesday's.
    """
    rows = db.execute(
        select(ProfileAnomaly.column_name, ProfileAnomaly.metric).where(
            ProfileAnomaly.target_id == target_id,
            ProfileAnomaly.kind == AnomalyKind.FINDING.value,
            ProfileAnomaly.acknowledged_at.is_not(None),
        )
    ).all()
    return {(r.column_name, r.metric) for r in rows}


def run_profile(db: Session, target_id: str) -> ProfileRun:
    target = db.get(ProfileTarget, target_id)
    if target is None:
        raise ValueError(f"Profile target {target_id} not found")
    connector_row = target.database.connector

    run = ProfileRun(id=cuid(), target_id=target.id, status=ProfileRunStatus.RUNNING.value, started_at=_utcnow())
    db.add(run)
    db.commit()

    started = time.monotonic()
    connector = None
    try:
        ref = parse_object(target.object)
        connector = build_connector(connector_row.type, connector_row.config)
        column_rows = connector.run_query(columns_sql(ref))
        if not column_rows:
            raise RuntimeError(
                f"{ref.qualified} has no readable columns - the table does not exist, or this "
                "connection's role cannot see it."
            )
        specs = [
            ColumnSpec(name=str(r["COLUMN_NAME"]), data_type=str(r["DATA_TYPE"]), ordinal=int(r["ORDINAL_POSITION"]))
            for r in column_rows
        ]
        row = connector.run_query(profile_sql(ref, specs))[0]

        row_count = int(row["ROW_COUNT"])
        for i, spec in enumerate(specs):
            a = aggregate_aliases(i)
            family = spec.family
            min_n, max_n = _float(row.get(a["min"])), _float(row.get(a["max"]))
            if family == TEMPORAL:
                min_v, max_v = row.get(a["min_display"]), row.get(a["max_display"])
            else:
                min_v, max_v = _display(min_n, family), _display(max_n, family)
            db.add(ColumnProfile(
                id=cuid(),
                run_id=run.id,
                target_id=target.id,
                column_name=spec.name,
                data_type=spec.data_type,
                family=family,
                ordinal=spec.ordinal,
                row_count=row_count,
                null_count=int(row[a["nulls"]]),
                distinct_count=_int(row.get(a["distinct"])),
                blank_count=_int(row.get(a["blanks"])),
                min_numeric=min_n,
                max_numeric=max_n,
                mean_numeric=_float(row.get(a["mean"])),
                min_value=None if min_v is None else str(min_v),
                max_value=None if max_v is None else str(max_v),
            ))
        db.flush()
        db.refresh(run, attribute_names=["columns"])

        run.row_count = row_count
        run.column_count = len(specs)
        run.status = ProfileRunStatus.SUCCEEDED.value

        history = [snapshot_of(r) for r in prior_runs(db, target.id, run)]
        drafts = detector.detect(snapshot_of(run), history, now_epoch=time.time())
        acknowledged = _acknowledged_keys(db, target.id)
        now = _utcnow()
        for d in drafts:
            carried = d.kind == AnomalyKind.FINDING.value and (d.column_name, d.metric) in acknowledged
            db.add(ProfileAnomaly(
                id=cuid(),
                target_id=target.id,
                run_id=run.id,
                column_name=d.column_name,
                kind=d.kind,
                metric=d.metric,
                severity=d.severity,
                observed=d.observed,
                expected=d.expected,
                lower=d.lower,
                upper=d.upper,
                message=d.message,
                acknowledged_at=now if carried else None,
            ))
        run.anomaly_count = len(drafts)
        learning = len(history) < detector.MIN_HISTORY
        run.message = (
            f"Profiled {len(specs)} column(s) over {row_count:,} row(s); {len(drafts)} finding(s)"
            + (f". Learning the baseline ({len(history)}/{detector.MIN_HISTORY} prior runs)" if learning else "")
        )
    except Exception as exc:  # noqa: BLE001 - a failed profile is recorded, never raised into the scheduler
        db.rollback()
        run = db.get(ProfileRun, run.id)
        run.status = ProfileRunStatus.ERROR.value
        run.message = explain_warehouse_error(exc, target.object.split(".")[0])[:2000]
        logger.warning("Profile of %s failed: %s", target.object, exc)
    finally:
        if connector is not None:
            connector.close()

    run.finished_at = _utcnow()
    run.duration_ms = int((time.monotonic() - started) * 1000)
    db.commit()
    db.refresh(run)
    return run


def list_tables(connector_type: str, connector_config: dict, database: str) -> list[str]:
    """Every base table in a database, fully qualified."""
    return [t["object"] for t in list_catalog(connector_type, connector_config, database)]


def list_catalog(connector_type: str, connector_config: dict, database: str) -> list[dict]:
    """Every base table in a database, with the warehouse's own row count.

    Read from `INFORMATION_SCHEMA.TABLES`, which is metadata: listing a
    database this way costs no scan, however large its tables are.
    """
    connector = build_connector(connector_type, connector_config)
    try:
        rows = connector.run_query(tables_sql(database))
    finally:
        connector.close()
    return [
        {
            "object": f"{database.upper()}.{r['TABLE_SCHEMA']}.{r['TABLE_NAME']}",
            "schema": str(r["TABLE_SCHEMA"]),
            "table": str(r["TABLE_NAME"]),
            "row_count": _int(r.get("ROW_COUNT")),
            "last_altered": r.get("LAST_ALTERED"),
        }
        for r in rows
    ]


def explain_warehouse_error(exc: Exception, database: str) -> str:
    """A warehouse error as a sentence a person can act on.

    Snowflake reports a missing database and an ungranted one identically
    ("does not exist or not authorized") - deliberately, so a role cannot probe
    for names it may not see. Both have the same fix from here, so say that
    instead of pasting the compiler's error code.
    """
    text = str(exc)
    if "does not exist or not authorized" in text:
        return (
            f"This connection cannot read {database}. Either the database no longer exists, or the "
            f"connection's role has not been granted it (GRANT USAGE ON DATABASE {database}, plus USAGE "
            "on its schemas and SELECT on its tables)."
        )
    return text.split("\n")[-1][:500]


def sync_database_catalog(db: Session, database) -> tuple[list[dict], str | None]:
    """Read one database's table list live from Snowflake and cache it.

    Returns `(tables, error)` in the same shape `list_catalog` returns, so a
    caller can treat a fresh read and a cached one identically. The cache row
    is written but not committed - callers batch several databases into one
    commit (or roll one back without half-writing the others).
    """
    try:
        tables = list_catalog(database.connector.type, database.connector.config, database.name)
        error = None
    except Exception as exc:  # noqa: BLE001 - one unreadable database must not fail the sync
        tables, error = [], explain_warehouse_error(exc, database.name)

    cached = db.scalars(select(DatabaseCatalogSync).where(DatabaseCatalogSync.database_id == database.id)).first()
    if cached is None:
        cached = DatabaseCatalogSync(database_id=database.id)
        db.add(cached)
    cached.readable = error is None
    cached.error = error
    # JSONB needs JSON-safe values; `last_altered` arrives as a datetime.
    cached.tables = [
        {**t, "last_altered": t["last_altered"].isoformat() if t["last_altered"] else None} for t in tables
    ]
    cached.synced_at = _utcnow()
    db.flush()
    return tables, error
