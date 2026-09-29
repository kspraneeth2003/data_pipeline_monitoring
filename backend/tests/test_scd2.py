"""SCD2 detection, derivation and the statement it produces.

The theme of these is that an SCD2 check is easy to make vacuous, and a vacuous
check is worse than a missing one because the coverage report then claims ground
nothing covers. Several of them exist only to pin that down.
"""

import pytest

from app.checks.config_schemas import Scd2IntegrityConfig
from app.checks.scd2 import build_scd2_sql
from app.ingest.heuristic import detect_scd2, propose_checks


def _table(fqn: str, columns: list[tuple[str, str]]) -> dict:
    database, schema, name = fqn.split(".")
    return {
        "database": database,
        "schema": schema,
        "name": name,
        "fqn": fqn,
        "comment": None,
        "file_path": "snowflake/x.sql",
        "columns": [
            {"name": n, "data_type": t, "nullable": True} for n, t in columns
        ],
    }


SCD2_COLUMNS = [
    ("MEMBER_KEY", "NUMBER"),
    ("MEMBER_ID", "NUMBER"),
    ("TIER", "STRING"),
    ("VALID_FROM", "TIMESTAMP_NTZ"),
    ("VALID_TO", "TIMESTAMP_NTZ"),
    ("IS_CURRENT", "BOOLEAN"),
]


def test_detects_the_type_2_shape():
    shape = detect_scd2(_table("DB.SILVER.MEMBERS", SCD2_COLUMNS))
    assert shape == {
        "valid_from": "VALID_FROM",
        "valid_to": "VALID_TO",
        "current_flag": "IS_CURRENT",
    }


def test_detects_alternative_spellings():
    shape = detect_scd2(
        _table(
            "DB.SILVER.DIM",
            [("ID", "NUMBER"), ("EFFECTIVE_FROM", "DATE"), ("EFFECTIVE_TO", "DATE"), ("IS_ACTIVE", "BOOLEAN")],
        )
    )
    assert shape["valid_from"] == "EFFECTIVE_FROM"
    assert shape["current_flag"] == "IS_ACTIVE"


@pytest.mark.parametrize(
    "columns",
    [
        # A start date with no end is an event date, not a validity window.
        [("ID", "NUMBER"), ("VALID_FROM", "DATE"), ("IS_CURRENT", "BOOLEAN")],
        # A flag with no window is a status column.
        [("ID", "NUMBER"), ("IS_ACTIVE", "BOOLEAN")],
        # A window with no flag cannot answer "which version is live".
        [("ID", "NUMBER"), ("VALID_FROM", "DATE"), ("VALID_TO", "DATE")],
    ],
)
def test_partial_shapes_are_not_scd2(columns):
    assert detect_scd2(_table("DB.SILVER.T", columns)) is None


def test_no_proposal_without_a_merge_to_name_the_natural_key():
    """The table alone cannot tell MEMBER_KEY from MEMBER_ID, and choosing the
    surrogate would make every assertion pass without testing anything."""
    parsed = {
        "schemas": {},
        "tables": [_table("DB.SILVER.MEMBERS", SCD2_COLUMNS)],
        "merges": [],
        "streams": {},
        "cadence": {},
        "warnings": [],
    }
    assert not [p for p in propose_checks(parsed) if p["type"] == "SCD2_INTEGRITY"]


def test_proposal_uses_the_natural_key_from_the_merge():
    parsed = {
        "schemas": {},
        "tables": [_table("DB.SILVER.MEMBERS", SCD2_COLUMNS)],
        "merges": [
            {
                "target": "DB.SILVER.MEMBERS",
                "source": "DB.BRONZE.MEMBERS_RAW",
                "key_columns": [
                    {"name": "MEMBER_ID", "source_expr": "s.MEMBER_ID", "target_expr": "MEMBER_ID"}
                ],
                "value_columns": [],
                "filtered": False,
                "filter_predicate": None,
                "file_path": "snowflake/x.sql",
            }
        ],
        "streams": {},
        "cadence": {},
        "warnings": [],
    }
    proposal = next(p for p in propose_checks(parsed) if p["type"] == "SCD2_INTEGRITY")
    assert proposal["config"]["naturalKeyColumns"] == ["MEMBER_ID"]
    # Not the surrogate, and the proposal says why out loud.
    assert "MEMBER_KEY" not in proposal["config"]["naturalKeyColumns"]
    assert "surrogate" in proposal["rationale"].lower()


def test_statement_counts_all_four_assertions_separately():
    """One query, four independently-readable counts. Folding them into a single
    "violations" total would lose which fix is needed."""
    sql = build_scd2_sql(
        Scd2IntegrityConfig(object="DB.SILVER.MEMBERS", naturalKeyColumns=["MEMBER_ID"])
    )
    for column in (
        "KEYS_WITH_NO_CURRENT",
        "KEYS_WITH_MANY_CURRENT",
        "OVERLAPPING_VERSIONS",
        "GAPPED_VERSIONS",
        "INVALID_WINDOWS",
    ):
        assert column in sql


def test_composite_natural_key_is_null_safe():
    """A null in any key part must not collapse those rows into one partition,
    which would report every one of them as overlapping with all the others."""
    sql = build_scd2_sql(
        Scd2IntegrityConfig(
            object="DB.SILVER.T", naturalKeyColumns=["ACCOUNT_ID", "REGION"]
        )
    )
    assert sql.count("NVL(TO_VARCHAR(") == 2


def test_null_sentinel_convention_changes_the_predicate():
    """The two conventions are not interchangeable, and the check has to be told
    which is in use rather than inferring it from the data it is auditing."""
    with_sentinel = build_scd2_sql(
        Scd2IntegrityConfig(object="T", naturalKeyColumns=["ID"])
    )
    with_null = build_scd2_sql(
        Scd2IntegrityConfig(object="T", naturalKeyColumns=["ID"], openEndedSentinel=None)
    )
    assert "9999-12-31" in with_sentinel
    assert "VALID_TO IS NULL" in with_null
    # An open window must still sort as the far future, or every comparison
    # against it is NULL and the check silently stops asserting anything.
    assert "COALESCE(VALID_TO" in with_null
