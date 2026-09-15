from dataclasses import dataclass, field

from app.connectors.base import Connector
from app.connectors.registry import build_connector
from app.checks.b2s_parity import build_parity_sql, value_column_names
from app.checks.config_schemas import (
    CONFIG_SCHEMAS_BY_TYPE,
    BronzeToSilverParityConfig,
    CrossSourceParityConfig,
    FreshnessConfig,
    NullRateConfig,
    RowCountConfig,
    SchemaDriftConfig,
)


@dataclass
class CheckOutcome:
    status: str  # PASSED | FAILED | ERROR
    metrics: dict = field(default_factory=dict)
    message: str = ""


def _connector_handle(connector_type: str, config: dict) -> Connector:
    return build_connector(connector_type, config)


def _run_row_count(connector: Connector, raw_config: dict) -> CheckOutcome:
    config = RowCountConfig.model_validate(raw_config)
    primary_row = connector.run_query(f"SELECT COUNT(*) AS CNT FROM {config.object}")[0]
    primary_count = primary_row["CNT"]

    if config.comparisonObject:
        comparison_row = connector.run_query(f"SELECT COUNT(*) AS CNT FROM {config.comparisonObject}")[0]
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
    row = connector.run_query(
        f"SELECT DATEDIFF('minute', MAX({config.timestampColumn}), CURRENT_TIMESTAMP()) AS AGE_MINUTES "
        f"FROM {config.object}"
    )[0]
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
    row = connector.run_query(
        f"SELECT COUNT(*) AS TOTAL, COUNT_IF({config.column} IS NULL) AS NULLS FROM {config.object}"
    )[0]
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


def _as_int(value) -> int:
    return int(value) if value is not None else 0


def _run_bronze_to_silver_parity(connector: Connector, raw_config: dict) -> CheckOutcome:
    config = BronzeToSilverParityConfig.model_validate(raw_config)
    row = connector.run_query(build_parity_sql(config))[0]

    missing = _as_int(row["MISSING_IN_SILVER"])
    extra = _as_int(row["EXTRA_IN_SILVER"])
    duplicate_keys = _as_int(row["SILVER_DUPLICATE_KEYS"])
    surplus_rows = _as_int(row["SILVER_SURPLUS_ROWS"])
    value_mismatches = _as_int(row["VALUE_MISMATCHES"])

    # Per-column mismatch counts, reported under the logical column name so a
    # failure points at the column rather than at an opaque total.
    mismatch_by_column = {
        logical: _as_int(row[metric])
        for metric, logical in value_column_names(config).items()
        if _as_int(row.get(metric)) > 0
    }

    def _sample(key: str) -> list:
        raw = row.get(key)
        if isinstance(raw, str):
            import json

            try:
                raw = json.loads(raw)
            except ValueError:
                return []
        return raw or []

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

    # Ordered worst-first: a dedup failure is the headline finding, since it is
    # the property this check exists to prove.
    failures: list[str] = []
    if duplicate_keys > config.maxDuplicateKeys:
        failures.append(
            f"deduplication failed - {duplicate_keys} key(s) appear more than once in silver "
            f"({surplus_rows} surplus row(s))"
        )
    if missing > config.maxMissingInSilver:
        failures.append(f"{missing} settled bronze key(s) never reached silver")
    if extra > config.maxExtraInSilver:
        failures.append(f"{extra} silver key(s) have no bronze origin")
    if value_mismatches > config.maxValueMismatches:
        detail = (
            " on " + ", ".join(f"{col} ({n})" for col, n in sorted(mismatch_by_column.items()))
            if mismatch_by_column
            else ""
        )
        failures.append(f"{value_mismatches} key(s) disagree on value{detail}")

    if failures:
        return CheckOutcome(
            status="FAILED",
            metrics=metrics,
            message=(
                f"{config.bronzeObject} -> {config.silverObject}: " + "; ".join(failures)
            ),
        )

    return CheckOutcome(
        status="PASSED",
        metrics=metrics,
        message=(
            f"{config.bronzeObject} -> {config.silverObject}: "
            f"{metrics['bronzeDistinctKeys']} bronze key(s) map 1:1 onto "
            f"{metrics['silverRows']} silver row(s); no duplicates, no loss"
            + (f", {len(config.valueColumns)} value column(s) agree" if config.valueColumns else "")
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
