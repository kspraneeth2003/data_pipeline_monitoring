"""Every check type can show the SQL it would run, and it is the real SQL.

The second half is what these tests are for. A `build_sql` that merely produces
plausible-looking SQL is worse than none: a reviewer approves the rendered
statement and the engine runs a different one. So the builders are asserted to
be the same functions the engine calls, not just to return something.
"""

import inspect

import pytest

from app.checks import engine
from app.checks.config_schemas import CONFIG_SCHEMAS_BY_TYPE
from app.checks.sql import (
    build_statements,
    freshness_sql,
    null_rate_sql,
    row_count_sql,
    try_build_statements,
)

CONFIGS = {
    "ROW_COUNT": {"object": "DB.SILVER.ORDERS", "minRows": 100},
    "FRESHNESS": {
        "object": "DB.SILVER.ORDERS",
        "timestampColumn": "UPDATED_AT",
        "maxAgeMinutes": 90,
    },
    "NULL_RATE": {
        "object": "DB.SILVER.ORDERS",
        "column": "CUSTOMER_ID",
        "maxNullRatio": 0.0,
    },
    "SCHEMA_DRIFT": {
        "object": "DB.SILVER.ORDERS",
        "expectedColumns": [{"name": "ORDER_ID", "dataType": "NUMBER"}],
    },
    "CROSS_SOURCE_PARITY": {
        "primaryQuery": "SELECT COUNT(*) FROM DB.SILVER.ORDERS",
        "secondaryQuery": "SELECT COUNT(*) FROM public.orders",
        "toleranceAbs": 0,
    },
    "SCD2_INTEGRITY": {
        "object": "DB.SILVER.ORDERS",
        "naturalKeyColumns": ["ORDER_ID"],
    },
    "BRONZE_TO_SILVER_PARITY": {
        "bronzeObject": "DB.BRONZE.ORDERS_RAW",
        "silverObject": "DB.SILVER.ORDERS",
        "keyColumns": [
            {
                "name": "ORDER_ID",
                "bronze": "RAW_PAYLOAD:order_id::NUMBER",
                "silver": "ORDER_ID",
            }
        ],
        "valueColumns": [
            {
                "name": "AMOUNT",
                "bronze": "RAW_PAYLOAD:amount::NUMBER",
                "silver": "AMOUNT",
            }
        ],
    },
}


def test_every_registered_check_type_has_a_builder():
    """A type the engine can run but cannot explain would be a check that
    silently opts out of the three-fields rule, so the coverage is asserted
    against the type registry rather than a hand-kept list."""
    for check_type in CONFIG_SCHEMAS_BY_TYPE:
        assert check_type in CONFIGS, f"no fixture config for {check_type}"
        statements = build_statements(check_type, CONFIGS[check_type])
        assert statements, f"{check_type} produced no statements"
        for statement in statements:
            assert statement.label.strip()
            assert statement.sql.strip()


@pytest.mark.parametrize("check_type", sorted(CONFIGS))
def test_statements_name_the_objects_they_touch(check_type):
    """A statement a reviewer cannot tie back to a table is not reviewable."""
    sql = " ".join(s.sql for s in build_statements(check_type, CONFIGS[check_type]))
    assert "ORDERS" in sql.upper()


def test_row_count_renders_both_sides_when_comparing():
    statements = build_statements(
        "ROW_COUNT",
        {"object": "DB.SILVER.ORDERS", "comparisonObject": "DB.BRONZE.ORDERS_RAW"},
    )
    assert len(statements) == 2
    assert "DB.SILVER.ORDERS" in statements[0].sql
    assert "DB.BRONZE.ORDERS_RAW" in statements[1].sql


def test_cross_source_statements_name_their_own_side():
    """The two run against different systems. Presenting them as one script
    would read as a join that cannot exist."""
    statements = build_statements("CROSS_SOURCE_PARITY", CONFIGS["CROSS_SOURCE_PARITY"])
    assert [s.connection for s in statements] == ["primary", "secondary"]


def test_engine_runs_the_statements_that_are_rendered():
    """The anti-drift assertion: the engine's query text comes from the same
    builders, so a rendered statement cannot diverge from an executed one."""
    source = inspect.getsource(engine)
    for builder in ("row_count_sql", "freshness_sql", "null_rate_sql"):
        assert f"{builder}(config)" in source or f"{builder}(" in source, builder
    # The single-object builders are the ones easiest to re-inline by accident,
    # so assert the engine holds no literal copy of them.
    assert "SELECT COUNT(*) AS CNT FROM" not in source
    assert "DATEDIFF('minute'" not in source
    assert "COUNT_IF(" not in source


def test_builders_produce_what_the_engine_expects():
    """The column aliases the engine reads out by name."""
    from app.checks.config_schemas import FreshnessConfig, NullRateConfig, RowCountConfig

    assert "AS CNT" in row_count_sql(RowCountConfig(object="T"))
    assert "AS AGE_MINUTES" in freshness_sql(
        FreshnessConfig(object="T", timestampColumn="TS", maxAgeMinutes=10)
    )
    null_sql = null_rate_sql(NullRateConfig(object="T", column="C", maxNullRatio=0))
    assert "AS TOTAL" in null_sql and "AS NULLS" in null_sql


def test_an_unbuildable_config_reports_rather_than_raises():
    """A broken check must not blank the list it appears in - the error is a
    finding about that check, shown next to it."""
    statements, error = try_build_statements("NULL_RATE", {"object": "T"})
    assert statements == []
    assert error and "column" in error.lower()


def test_an_unknown_type_is_an_error_not_an_empty_list():
    """Empty would read as "this check runs nothing", which is not what a
    misspelled type means."""
    statements, error = try_build_statements("NOT_A_TYPE", {})
    assert statements == []
    assert "NOT_A_TYPE" in error
