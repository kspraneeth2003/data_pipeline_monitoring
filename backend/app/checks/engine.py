from dataclasses import dataclass, field

from app.connectors.base import Connector
from app.connectors.registry import build_connector
from app.checks.b2s_parity import build_parity_sql, value_column_names
from app.checks.scd2 import build_scd2_sql
from app.checks.sql import freshness_sql, null_rate_sql, row_count_sql
from app.checks.config_schemas import (
    CONFIG_SCHEMAS_BY_TYPE,
    BronzeToSilverParityConfig,
    CrossSourceParityConfig,
    FreshnessConfig,
    NullRateConfig,
    RowCountConfig,
    SchemaDriftConfig,
    Scd2IntegrityConfig,
)


@dataclass
class CheckOutcome:
    status: str  # PASSED | FAILED | ERROR | INVALID (parity on nothing); runner.py may also make INVALID or UNREACHABLE
    metrics: dict = field(default_factory=dict)
    message: str = ""


def _connector_handle(connector_type: str, config: dict) -> Connector:
    return build_connector(connector_type, config)


def _run_row_count(connector: Connector, raw_config: dict) -> CheckOutcome:
    config = RowCountConfig.model_validate(raw_config)
    primary_row = connector.run_query(row_count_sql(config))[0]
    primary_count = primary_row["CNT"]

    if config.comparisonObject:
        comparison_row = connector.run_query(
            row_count_sql(RowCountConfig(object=config.comparisonObject))
        )[0]
        comparison_count = comparison_row["CNT"]
        diff = abs(primary_count - comparison_count)
        passed = diff <= config.toleranceAbs
        return CheckOutcome(
            status="PASSED" if passed else "FAILED",
            metrics={
                "primaryCount": primary_count,
                "comparisonCount": comparison_count,
                "diff": diff,
                "toleranceAbs": config.toleranceAbs,
            },
            message=(
                f"{config.object} ({primary_count}) matches {config.comparisonObject} "
                f"({comparison_count}) within tolerance {config.toleranceAbs}"
                if passed
                else f"Row count mismatch: {config.object}={primary_count} vs "
                f"{config.comparisonObject}={comparison_count} (diff {diff} > tolerance {config.toleranceAbs})"
            ),
        )

    min_rows = config.minRows or 0
    passed = primary_count >= min_rows
    return CheckOutcome(
        status="PASSED" if passed else "FAILED",
        metrics={"primaryCount": primary_count, "minRows": min_rows},
        message=(
            f"{config.object} has {primary_count} rows (>= {min_rows})"
            if passed
            else f"{config.object} has {primary_count} rows, below minimum {min_rows}"
        ),
    )


def _run_freshness(connector: Connector, raw_config: dict) -> CheckOutcome:
    config = FreshnessConfig.model_validate(raw_config)
    row = connector.run_query(freshness_sql(config))[0]
    age_minutes = row["AGE_MINUTES"]

    if age_minutes is None:
        return CheckOutcome(
            status="FAILED",
            metrics={"ageMinutes": None, "maxAgeMinutes": config.maxAgeMinutes},
            message=f"{config.object} has no rows - cannot evaluate freshness on {config.timestampColumn}",
        )

    passed = age_minutes <= config.maxAgeMinutes
    return CheckOutcome(
        status="PASSED" if passed else "FAILED",
        metrics={"ageMinutes": age_minutes, "maxAgeMinutes": config.maxAgeMinutes},
        message=(
            f"{config.object}.{config.timestampColumn} is {age_minutes}m old (<= {config.maxAgeMinutes}m)"
            if passed
            else f"{config.object}.{config.timestampColumn} is {age_minutes}m old, exceeding {config.maxAgeMinutes}m"
        ),
    )


def _run_null_rate(connector: Connector, raw_config: dict) -> CheckOutcome:
    config = NullRateConfig.model_validate(raw_config)
    row = connector.run_query(null_rate_sql(config))[0]
    total, nulls = row["TOTAL"], row["NULLS"]
    ratio = 0 if total == 0 else nulls / total
    passed = ratio <= config.maxNullRatio

    return CheckOutcome(
        status="PASSED" if passed else "FAILED",
        metrics={"total": total, "nulls": nulls, "ratio": ratio, "maxNullRatio": config.maxNullRatio},
        message=(
            f"{config.object}.{config.column} null ratio {ratio * 100:.2f}% (<= {config.maxNullRatio * 100:.2f}%)"
            if passed
            else f"{config.object}.{config.column} null ratio {ratio * 100:.2f}% exceeds {config.maxNullRatio * 100:.2f}%"
        ),
    )


def _run_schema_drift(connector: Connector, raw_config: dict) -> CheckOutcome:
    config = SchemaDriftConfig.model_validate(raw_config)
    schema = connector.get_schema(config.object)
    actual_by_name = {c["name"].upper(): c for c in schema["columns"]}

    missing: list[str] = []
    type_mismatches: list[str] = []

    for expected in config.expectedColumns:
        actual = actual_by_name.get(expected.name.upper())
        if not actual:
            missing.append(expected.name)
            continue
        if not actual["data_type"].upper().startswith(expected.dataType.upper()):
            type_mismatches.append(f"{expected.name} expected {expected.dataType}, got {actual['data_type']}")

    expected_names = {e.name.upper() for e in config.expectedColumns}
    extra = [c["name"] for c in schema["columns"] if c["name"].upper() not in expected_names]

    passed = not missing and not type_mismatches
    parts = []
    if missing:
        parts.append(f"missing [{', '.join(missing)}]")
    if type_mismatches:
        parts.append(f"type mismatches [{'; '.join(type_mismatches)}]")

    return CheckOutcome(
        status="PASSED" if passed else "FAILED",
        metrics={"missing": missing, "extra": extra, "typeMismatches": type_mismatches},
        message=(
            f"{config.object} schema matches expected definition"
            if passed
            else f"{config.object} schema drift detected: {'; '.join(parts)}"
        ),
    )


def _run_cross_source_parity(primary: Connector, secondary: Connector, raw_config: dict) -> CheckOutcome:
    config = CrossSourceParityConfig.model_validate(raw_config)
    primary_row = primary.run_query(config.primaryQuery)[0]
    secondary_row = secondary.run_query(config.secondaryQuery)[0]

    primary_value = float(list(primary_row.values())[0])
    secondary_value = float(list(secondary_row.values())[0])
    diff = abs(primary_value - secondary_value)
    passed = diff <= config.toleranceAbs

    return CheckOutcome(
        status="PASSED" if passed else "FAILED",
        metrics={
            "primaryValue": primary_value,
            "secondaryValue": secondary_value,
            "diff": diff,
            "toleranceAbs": config.toleranceAbs,
        },
        message=(
            f"Primary ({primary_value}) matches secondary ({secondary_value}) within tolerance {config.toleranceAbs}"
            if passed
            else f"Parity mismatch: primary={primary_value} secondary={secondary_value} "
            f"(diff {diff} > tolerance {config.toleranceAbs})"
        ),
    )


def _json_list(raw) -> list:
    """A Snowflake ARRAY_AGG result as a Python list.

    The connector returns an array either already decoded or as its JSON text,
    depending on the column and the driver version. Both are normal, so both are
    accepted; anything that will not parse becomes an empty list rather than an
    exception, because a sample is an aid to diagnosis and losing it must not
    cost the counts it came with.
    """
    if isinstance(raw, str):
        import json

        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    return raw or []


def _as_int(value) -> int:
    return int(value) if value is not None else 0


def _fmt_minutes(minutes: float) -> str:
    return str(int(minutes)) if float(minutes).is_integer() else f"{minutes:g}"


@dataclass
class _Breach:
    metric: str
    value: int
    threshold: int
    text: str


def _partition(breaches: list[_Breach], report_only: list[str]) -> tuple[list[str], dict]:
    """Split threshold breaches into those that fail the run and those reported.

    A report-only breach is still recorded under `metrics["reported"]` with its
    value and threshold, so the run says what it saw - demoting a metric changes
    the verdict, never the observation.
    """
    demoted = set(report_only)
    asserted = [b.text for b in breaches if b.metric not in demoted]
    reported = {
        b.metric: {"value": b.value, "threshold": b.threshold, "text": b.text}
        for b in breaches
        if b.metric in demoted
    }
    return asserted, reported


def _reported_suffix(reported: dict) -> str:
    if not reported:
        return ""
    return " (reported only: " + "; ".join(r["text"] for r in reported.values()) + ")"


def _run_bronze_to_silver_parity(connector: Connector, raw_config: dict) -> CheckOutcome:
    config = BronzeToSilverParityConfig.model_validate(raw_config)
    row = connector.run_query(build_parity_sql(config))[0]

    missing = _as_int(row["MISSING_IN_SILVER"])
    extra = _as_int(row["EXTRA_IN_SILVER"])
    duplicate_keys = _as_int(row["SILVER_DUPLICATE_KEYS"])
    surplus_rows = _as_int(row["SILVER_SURPLUS_ROWS"])
    value_mismatches = _as_int(row["VALUE_MISMATCHES"])
    # Landed in bronze after the cutoff and already merged - normal pipeline
    # latency, reported for visibility but never a failure.
    silver_ahead = _as_int(row.get("SILVER_AHEAD_OF_SETTLED"))

    # Per-column mismatch counts, reported under the logical column name so a
    # failure points at the column rather than at an opaque total.
    mismatch_by_column = {
        logical: _as_int(row[metric])
        for metric, logical in value_column_names(config).items()
        if _as_int(row.get(metric)) > 0
    }

    def _sample(key: str) -> list:
        return _json_list(row.get(key))

    metrics = {
        "bronzeRowsSettled": _as_int(row["BRONZE_ROWS_SETTLED"]),
        "bronzeDistinctKeys": _as_int(row["BRONZE_DISTINCT_KEYS"]),
        "silverRows": _as_int(row["SILVER_ROWS"]),
        "silverDistinctKeys": _as_int(row["SILVER_DISTINCT_KEYS"]),
        "silverDuplicateKeys": duplicate_keys,
        "silverSurplusRows": surplus_rows,
        "missingInSilver": missing,
        "extraInSilver": extra,
        "valueMismatches": value_mismatches,
        "silverAheadOfSettled": silver_ahead,
        "mismatchByColumn": mismatch_by_column,
        "lagMinutes": config.lagMinutes,
        "thresholds": {
            "maxMissingInSilver": config.maxMissingInSilver,
            "maxExtraInSilver": config.maxExtraInSilver,
            "maxDuplicateKeys": config.maxDuplicateKeys,
            "maxValueMismatches": config.maxValueMismatches,
        },
        "samples": {
            "missingInSilver": _sample("SAMPLE_MISSING"),
            "extraInSilver": _sample("SAMPLE_EXTRA"),
            "duplicateKeys": _sample("SAMPLE_DUPLICATE_KEYS"),
            "valueMismatch": _sample("SAMPLE_VALUE_MISMATCH"),
        },
    }

    if metrics["bronzeDistinctKeys"] == 0 and silver_ahead > 0:
        # Every source row is inside the settling window, so nothing was
        # compared - and a pass here would be green for having looked at
        # nothing. The usual cause is a source rebuilt by TRUNCATE + INSERT
        # every run, whose timestamp is always "just now": it has no per-row
        # settle time for a lag to wait out. That is a limit of this check, not
        # news about the pipeline, so it reads as not monitored (FLOW.md §2.1).
        return CheckOutcome(
            status="INVALID",
            metrics=metrics,
            message=(
                f"Not monitored: every row of {config.bronzeObject} is newer than the "
                f"{_fmt_minutes(config.lagMinutes)}-minute settling window, so nothing could be "
                f"compared. Its {config.bronzeLoadedAtColumn} is probably rewritten on every run "
                f"(a table rebuilt with TRUNCATE + INSERT), which leaves no per-row settle time."
            ),
        )

    # Ordered worst-first: a dedup failure is the headline finding, since it is
    # the property this check exists to prove.
    breaches: list[_Breach] = []
    if duplicate_keys > config.maxDuplicateKeys:
        breaches.append(_Breach(
            "duplicateKeys", duplicate_keys, config.maxDuplicateKeys,
            f"deduplication failed - {duplicate_keys} key(s) appear more than once in silver "
            f"({surplus_rows} surplus row(s))",
        ))
    if missing > config.maxMissingInSilver:
        breaches.append(_Breach(
            "missingInSilver", missing, config.maxMissingInSilver,
            f"{missing} settled bronze key(s) never reached silver",
        ))
    if extra > config.maxExtraInSilver:
        breaches.append(_Breach(
            "extraInSilver", extra, config.maxExtraInSilver,
            f"{extra} silver key(s) have no bronze origin",
        ))
    if value_mismatches > config.maxValueMismatches:
        detail = (
            " on " + ", ".join(f"{col} ({n})" for col, n in sorted(mismatch_by_column.items()))
            if mismatch_by_column
            else ""
        )
        breaches.append(_Breach(
            "valueMismatches", value_mismatches, config.maxValueMismatches,
            f"{value_mismatches} key(s) disagree on value{detail}",
        ))

    failures, reported = _partition(breaches, config.reportOnly)
    metrics["reportOnly"] = list(config.reportOnly)
    metrics["reported"] = reported
    prefix = f"{config.bronzeObject} -> {config.silverObject}: "

    if failures:
        return CheckOutcome(
            status="FAILED",
            metrics=metrics,
            message=prefix + "; ".join(failures) + _reported_suffix(reported),
        )

    if reported:
        # The 1:1 sentence below would be false here - something *was* off,
        # it is just not what this check asserts.
        return CheckOutcome(
            status="PASSED",
            metrics=metrics,
            message=prefix + "no asserted metric breached" + _reported_suffix(reported),
        )

    return CheckOutcome(
        status="PASSED",
        metrics=metrics,
        message=(
            prefix
            + f"{metrics['bronzeDistinctKeys']} settled bronze key(s) map 1:1 onto "
            f"{metrics['silverRows']} silver row(s); no duplicates, no loss"
            + (f" ({silver_ahead} still settling)" if silver_ahead else "")
            + (f", {len(config.valueColumns)} value column(s) agree" if config.valueColumns else "")
        ),
    )



def _run_scd2_integrity(connector: Connector, raw_config: dict) -> CheckOutcome:
    config = Scd2IntegrityConfig.model_validate(raw_config)
    row = connector.run_query(build_scd2_sql(config))[0]

    no_current = _as_int(row["KEYS_WITH_NO_CURRENT"])
    many_current = _as_int(row["KEYS_WITH_MANY_CURRENT"])
    overlapping = _as_int(row["OVERLAPPING_VERSIONS"])
    gapped = _as_int(row["GAPPED_VERSIONS"])
    invalid = _as_int(row["INVALID_WINDOWS"])
    current_not_open = _as_int(row["CURRENT_NOT_OPEN_ENDED"])
    open_not_current = _as_int(row["OPEN_ENDED_NOT_CURRENT"])

    metrics = {
        "totalRows": _as_int(row["TOTAL_ROWS"]),
        "totalKeys": _as_int(row["TOTAL_KEYS"]),
        "keysWithNoCurrent": no_current,
        "keysWithManyCurrent": many_current,
        "overlappingVersions": overlapping,
        "gappedVersions": gapped,
        "invalidWindows": invalid,
        "currentNotOpenEnded": current_not_open,
        "openEndedNotCurrent": open_not_current,
        "samples": {
            "currentFlagKeys": _json_list(row.get("SAMPLE_CURRENT_FLAG_KEYS")),
            "overlappingKeys": _json_list(row.get("SAMPLE_OVERLAPPING_KEYS")),
            "gappedKeys": _json_list(row.get("SAMPLE_GAPPED_KEYS")),
        },
        "thresholds": {
            "maxKeysWithNoCurrent": config.maxKeysWithNoCurrent,
            "maxKeysWithManyCurrent": config.maxKeysWithManyCurrent,
            "maxOverlappingVersions": config.maxOverlappingVersions,
            "maxGappedVersions": config.maxGappedVersions,
            "maxInvalidWindows": config.maxInvalidWindows,
        },
    }

    # Ordered by how badly each one corrupts a downstream join. Two current rows
    # doubles every measure joined through the dimension, which is the failure
    # that reaches a report unnoticed; a malformed window is usually visible the
    # moment anyone looks at the row.
    breaches: list[_Breach] = []
    if many_current > config.maxKeysWithManyCurrent:
        breaches.append(_Breach(
            "keysWithManyCurrent", many_current, config.maxKeysWithManyCurrent,
            f"{many_current} key(s) have more than one current row - any join through this "
            f"dimension fans out and doubles its measures",
        ))
    if no_current > config.maxKeysWithNoCurrent:
        breaches.append(_Breach(
            "keysWithNoCurrent", no_current, config.maxKeysWithNoCurrent,
            f"{no_current} key(s) have no current row, so they resolve to nothing in an as-of join",
        ))
    if overlapping > config.maxOverlappingVersions:
        breaches.append(_Breach(
            "overlappingVersions", overlapping, config.maxOverlappingVersions,
            f"{overlapping} version(s) overlap the next version of the same key",
        ))
    if gapped > config.maxGappedVersions:
        breaches.append(_Breach(
            "gappedVersions", gapped, config.maxGappedVersions,
            f"{gapped} gap(s) between consecutive versions - the key existed but no version covers "
            f"the interval",
        ))
    if invalid > config.maxInvalidWindows:
        breaches.append(_Breach(
            "invalidWindows", invalid, config.maxInvalidWindows,
            f"{invalid} row(s) have VALID_FROM at or after VALID_TO",
        ))

    failures, reported = _partition(breaches, config.reportOnly)
    if current_not_open or open_not_current:
        # Always asserted - see Scd2IntegrityConfig.reportOnly.
        failures.append(
            f"the current flag and the open-ended marker disagree on "
            f"{current_not_open + open_not_current} row(s)"
        )
    metrics["reportOnly"] = list(config.reportOnly)
    metrics["reported"] = reported

    if failures:
        return CheckOutcome(
            status="FAILED",
            metrics=metrics,
            message=f"{config.object}: " + "; ".join(failures) + _reported_suffix(reported),
        )

    if reported:
        return CheckOutcome(
            status="PASSED",
            metrics=metrics,
            message=f"{config.object}: no asserted metric breached" + _reported_suffix(reported),
        )

    return CheckOutcome(
        status="PASSED",
        metrics=metrics,
        message=(
            f"{config.object}: {metrics['totalKeys']} key(s) across {metrics['totalRows']} version(s) - "
            f"exactly one current row each, no overlaps, no gaps, all windows well-formed"
        ),
    )


def run_check(
    check_type: str,
    config: dict,
    connector_type: str,
    connector_config: dict,
    secondary_connector_type: str | None = None,
    secondary_connector_config: dict | None = None,
) -> CheckOutcome:
    primary = _connector_handle(connector_type, connector_config)
    secondary = (
        _connector_handle(secondary_connector_type, secondary_connector_config)
        if secondary_connector_type
        else None
    )

    try:
        if check_type == "ROW_COUNT":
            return _run_row_count(primary, config)
        if check_type == "FRESHNESS":
            return _run_freshness(primary, config)
        if check_type == "NULL_RATE":
            return _run_null_rate(primary, config)
        if check_type == "SCHEMA_DRIFT":
            return _run_schema_drift(primary, config)
        if check_type == "BRONZE_TO_SILVER_PARITY":
            return _run_bronze_to_silver_parity(primary, config)
        if check_type == "SCD2_INTEGRITY":
            return _run_scd2_integrity(primary, config)
        if check_type == "CROSS_SOURCE_PARITY":
            if secondary is None:
                raise ValueError("CROSS_SOURCE_PARITY checks require a secondaryConnectorId")
            return _run_cross_source_parity(primary, secondary, config)
        raise ValueError(f"Unsupported check type: {check_type}")
    except Exception as error:  # noqa: BLE001 - deliberately broad, becomes an ERROR run
        return CheckOutcome(status="ERROR", metrics={}, message=str(error))
    finally:
        primary.close()
        if secondary is not None:
            secondary.close()
