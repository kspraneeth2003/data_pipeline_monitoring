"""The statements a check runs, as text, without running it.

Every check owes its reader three things (see `FLOW.md` §2): a description, the
logic behind it, and the SQL. The first two are prose stored on the row; this
module is the third.

The rule that makes it worth having is that these are not *descriptions* of the
queries - they are the queries. `engine.py` builds its statements by calling in
here, so a rendered statement and an executed one cannot drift apart. A second,
"documentation" copy of the SQL would be wrong within a release and wrong
silently, which is worse than showing nothing.

Some checks run more than one statement, and one (`SCHEMA_DRIFT`) runs a
`DESCRIBE` rather than a query. So the unit is a labelled statement, not a
string: the label says what that statement establishes, which is what makes a
multi-statement check readable at all.
"""

from dataclasses import dataclass

from app.checks.b2s_parity import build_parity_sql
from app.checks.scd2 import build_scd2_sql
from app.checks.config_schemas import (
    BronzeToSilverParityConfig,
    CrossSourceParityConfig,
    FreshnessConfig,
    NullRateConfig,
    RowCountConfig,
    SchemaDriftConfig,
    Scd2IntegrityConfig,
)


@dataclass(frozen=True)
class CheckStatement:
    """One statement the check issues, and what it establishes.

    `connection` names which side it runs against - "primary" for everything
    single-source, and "primary"/"secondary" for a cross-source check, where
    the two statements run against different systems and reading them as one
    script would be a misunderstanding.
    """

    label: str
    sql: str
    connection: str = "primary"


def row_count_sql(config: RowCountConfig) -> str:
    return f"SELECT COUNT(*) AS CNT FROM {config.object}"


def freshness_sql(config: FreshnessConfig) -> str:
    return (
        f"SELECT DATEDIFF('minute', MAX({config.timestampColumn}), CURRENT_TIMESTAMP()) "
        f"AS AGE_MINUTES FROM {config.object}"
    )


def null_rate_sql(config: NullRateConfig) -> str:
    return (
        f"SELECT COUNT(*) AS TOTAL, COUNT_IF({config.column} IS NULL) AS NULLS "
        f"FROM {config.object}"
    )


def schema_describe_sql(config: SchemaDriftConfig) -> str:
    return f"DESCRIBE TABLE {config.object}"


def _row_count_statements(raw_config: dict) -> list[CheckStatement]:
    config = RowCountConfig.model_validate(raw_config)
    statements = [
        CheckStatement(
            label=f"Row count of {config.object}",
            sql=row_count_sql(config),
        )
    ]
    if config.comparisonObject:
        statements.append(
            CheckStatement(
                label=f"Row count of {config.comparisonObject}, compared within "
                f"{config.toleranceAbs} row(s)",
                sql=row_count_sql(
                    RowCountConfig(object=config.comparisonObject)
                ),
            )
        )
    return statements


def _freshness_statements(raw_config: dict) -> list[CheckStatement]:
    config = FreshnessConfig.model_validate(raw_config)
    return [
        CheckStatement(
            label=f"Age of the newest {config.timestampColumn}, against a "
            f"{config.maxAgeMinutes:g} minute limit",
            sql=freshness_sql(config),
        )
    ]


def _null_rate_statements(raw_config: dict) -> list[CheckStatement]:
    config = NullRateConfig.model_validate(raw_config)
    return [
        CheckStatement(
            label=f"Null count in {config.column}, against a "
            f"{config.maxNullRatio * 100:g}% limit",
            sql=null_rate_sql(config),
        )
    ]


def _schema_drift_statements(raw_config: dict) -> list[CheckStatement]:
    config = SchemaDriftConfig.model_validate(raw_config)
    return [
        CheckStatement(
            label=f"Live column contract of {config.object}, compared against the "
            f"{len(config.expectedColumns)} expected column(s)",
            sql=schema_describe_sql(config),
        )
    ]


def _cross_source_statements(raw_config: dict) -> list[CheckStatement]:
    config = CrossSourceParityConfig.model_validate(raw_config)
    return [
        CheckStatement(
            label="Primary side",
            sql=config.primaryQuery,
            connection="primary",
        ),
        CheckStatement(
            label=f"Secondary side, compared within {config.toleranceAbs:g}",
            sql=config.secondaryQuery,
            connection="secondary",
        ),
    ]


def _parity_statements(raw_config: dict) -> list[CheckStatement]:
    config = BronzeToSilverParityConfig.model_validate(raw_config)
    return [
        CheckStatement(
            label=f"Key and value parity, {config.bronzeObject} -> {config.silverObject}",
            sql=build_parity_sql(config),
        )
    ]


def _scd2_statements(raw_config: dict) -> list[CheckStatement]:
    config = Scd2IntegrityConfig.model_validate(raw_config)
    keys = ", ".join(config.naturalKeyColumns)
    return [
        CheckStatement(
            label=f"SCD2 integrity of {config.object}, keyed on {keys}: current-row count, "
            f"window overlaps, gaps and malformed windows",
            sql=build_scd2_sql(config),
        )
    ]


_BUILDERS = {
    "ROW_COUNT": _row_count_statements,
    "FRESHNESS": _freshness_statements,
    "NULL_RATE": _null_rate_statements,
    "SCHEMA_DRIFT": _schema_drift_statements,
    "CROSS_SOURCE_PARITY": _cross_source_statements,
    "BRONZE_TO_SILVER_PARITY": _parity_statements,
    "SCD2_INTEGRITY": _scd2_statements,
}


def build_statements(check_type: str, config: dict) -> list[CheckStatement]:
    """The statements this check would run, built from its stored config.

    Raises on an unknown type or a config that fails its schema, and that is
    deliberate: a check whose SQL cannot be built is a check that will ERROR on
    its first run, and finding that out during review is the whole point.
    """
    builder = _BUILDERS.get(check_type)
    if builder is None:
        raise ValueError(f"Unsupported check type: {check_type}")
    return builder(config)


def try_build_statements(check_type: str, config: dict) -> tuple[list[CheckStatement], str | None]:
    """`build_statements`, but reporting failure rather than raising.

    For list views and proposal review, where one unbuildable check must not
    blank the page - the error belongs next to that check, as the finding it is.
    """
    try:
        return build_statements(check_type, config), None
    except Exception as error:  # noqa: BLE001 - surfaced to the reader verbatim
        return [], str(error)
