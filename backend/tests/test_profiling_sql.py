"""The profile SQL: one statement, safe identifiers, and never a text value."""

import pytest

from app.profiling.sql import (
    BOOLEAN,
    NUMERIC,
    OTHER,
    TEMPORAL,
    TEXT,
    ColumnSpec,
    parse_object,
    profile_sql,
    type_family,
)


@pytest.mark.parametrize(
    "data_type,family",
    [
        ("NUMBER", NUMERIC), ("NUMBER(38,0)", NUMERIC), ("FLOAT", NUMERIC),
        ("TEXT", TEXT), ("VARCHAR(16777216)", TEXT),
        ("DATE", TEMPORAL), ("TIMESTAMP_NTZ", TEMPORAL), ("TIMESTAMP_TZ", TEMPORAL),
        ("BOOLEAN", BOOLEAN), ("VARIANT", OTHER), ("TIME", OTHER),
    ],
)
def test_type_family(data_type, family):
    assert type_family(data_type) == family


def test_parse_object_accepts_plain_names_and_upper_cases_them():
    ref = parse_object("dpm_src_crm.silver.customers")
    assert ref.qualified == "DPM_SRC_CRM.SILVER.CUSTOMERS"


@pytest.mark.parametrize(
    "bad", ["SILVER.CUSTOMERS", "A.B.C.D", "DB.SCHEMA.T; DROP TABLE X", 'DB.SCHEMA."quoted"', "DB..T"]
)
def test_parse_object_refuses_anything_but_three_plain_identifiers(bad):
    with pytest.raises(ValueError):
        parse_object(bad)


def test_profile_is_one_select_and_profiles_text_by_length_only():
    sql = profile_sql(
        parse_object("DB.S.T"),
        [ColumnSpec("EMAIL", "TEXT", 1), ColumnSpec("AMOUNT", "NUMBER", 2), ColumnSpec("PAYLOAD", "VARIANT", 3)],
    )
    assert sql.count("SELECT") == 1
    assert sql.rstrip().endswith("FROM DB.S.T")
    # Text is measured, never read: no MIN/MAX directly over the text column.
    assert 'MIN("EMAIL")' not in sql and 'MAX("EMAIL")' not in sql
    assert 'MIN(LENGTH("EMAIL"))' in sql
    assert 'MIN("AMOUNT")::FLOAT' in sql
    # Semi-structured columns get a null count and nothing else.
    assert 'COUNT_IF("PAYLOAD" IS NULL)' in sql
    assert 'APPROX_COUNT_DISTINCT("PAYLOAD")' not in sql


def test_column_names_are_quoted_verbatim():
    sql = profile_sql(parse_object("DB.S.T"), [ColumnSpec('odd "name"', "NUMBER", 1)])
    assert '"odd ""name"""' in sql
