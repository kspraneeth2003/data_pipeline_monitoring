"""Parity for a MERGE whose key comes from a joined table.

A table-to-table check cannot name such a key, which used to leave the table
"not monitored" for good. Reading the MERGE's own USING query as the source
side fixes that, but only if the rewrite keeps the query's meaning - so the
refusals are tested as carefully as the happy path.
"""

import pytest
from pydantic import ValidationError

from app.checks import engine
from app.checks.b2s_parity import build_parity_sql
from app.checks.config_schemas import BronzeToSilverParityConfig
from app.checks.defects import referenced_columns
from app.ingest.ddl_parser import parse_merges, parse_streams, parse_tables
from app.ingest.heuristic import SOURCE_QUERY_SETTLE_COLUMN, _parity_proposal, _source_query

DDL = """
CREATE TABLE DB.SILVER.POINTS (MEMBER_ID NUMBER, SNAPSHOT_DATE DATE, POINTS NUMBER, UPDATED_AT TIMESTAMP_NTZ);
CREATE TABLE DB.ID.XREF (SOURCE_ID STRING, INDIVIDUAL_ID STRING);
CREATE TABLE DB.GOLD.DAILY (INDIVIDUAL_ID STRING, SNAPSHOT_DATE DATE, POINTS NUMBER, UPDATED_AT TIMESTAMP_NTZ);
CREATE STREAM DB.SILVER.POINTS_STREAM ON TABLE DB.SILVER.POINTS;
"""

MERGE = """
MERGE INTO DB.GOLD.DAILY tgt
USING (
  SELECT x.INDIVIDUAL_ID AS INDIVIDUAL_ID, p.SNAPSHOT_DATE AS SNAPSHOT_DATE, p.POINTS AS POINTS
  FROM DB.SILVER.POINTS p
  JOIN DB.ID.XREF x ON x.SOURCE_ID = TO_VARCHAR(p.MEMBER_ID)
  WHERE p.MEMBER_ID IN (SELECT MEMBER_ID FROM DB.SILVER.POINTS_STREAM)
) src
ON tgt.INDIVIDUAL_ID = src.INDIVIDUAL_ID AND tgt.SNAPSHOT_DATE = src.SNAPSHOT_DATE
WHEN MATCHED THEN UPDATE SET POINTS = src.POINTS
WHEN NOT MATCHED THEN INSERT (INDIVIDUAL_ID, SNAPSHOT_DATE, POINTS)
  VALUES (src.INDIVIDUAL_ID, src.SNAPSHOT_DATE, src.POINTS);
"""


def _setup(merge_sql=MERGE):
    tables = {t["fqn"]: t for t in parse_tables(DDL, "x.sql")}
    merge = parse_merges(merge_sql, "gold.sql")[0]
    return merge, tables, parse_streams(DDL)


def test_joined_key_gets_a_query_sourced_proposal():
    merge, tables, streams = _setup()
    proposal = _parity_proposal(merge, tables, streams)

    assert proposal is not None
    config = proposal["config"]
    assert config["bronzeObject"] == "DB.SILVER.POINTS"
    assert config["bronzeLoadedAtColumn"] == SOURCE_QUERY_SETTLE_COLUMN
    assert [k["bronze"] for k in config["keyColumns"]] == ["INDIVIDUAL_ID", "SNAPSHOT_DATE"]
    assert [v["name"] for v in config["valueColumns"]] == ["POINTS"]
    # The query already applies the MERGE's WHERE; lifting it again would name
    # aliases that are out of scope.
    assert config["sourceFilter"] is None
    BronzeToSilverParityConfig.model_validate(config)


def test_streams_are_read_as_their_tables_and_the_settle_time_is_projected():
    merge, tables, streams = _setup()
    query, reason = _source_query(merge, tables, streams, "UPDATED_AT")

    assert reason is None
    assert "POINTS_STREAM" not in query.sql
    assert "FROM DB.SILVER.POINTS)" in query.sql
    assert query.sql.lstrip().startswith(f"SELECT p.UPDATED_AT AS {SOURCE_QUERY_SETTLE_COLUMN},")


def test_the_sql_reads_the_query_on_both_source_ctes():
    merge, tables, streams = _setup()
    config = BronzeToSilverParityConfig.model_validate(_parity_proposal(merge, tables, streams)["config"])
    sql = build_parity_sql(config)
    assert sql.count(") src") == 2  # settled and pending both read the query
    assert "FROM DB.SILVER.POINTS\n" not in sql


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("WHERE p.MEMBER_ID IN (SELECT MEMBER_ID FROM DB.SILVER.POINTS_STREAM)",
         None),
        ("WHERE METADATA$ACTION = 'INSERT'", "stream metadata"),
        ("WHERE p.MEMBER_ID > :last_id", "procedure variables"),
    ],
)
def test_refusals_that_would_change_the_querys_meaning(change, reason):
    merge_sql = MERGE.replace("WHERE p.MEMBER_ID IN (SELECT MEMBER_ID FROM DB.SILVER.POINTS_STREAM)", change)
    merge, tables, streams = _setup(merge_sql)
    query, why = _source_query(merge, tables, streams, "UPDATED_AT")
    if reason is None:
        assert query is not None
    else:
        assert query is None and reason in why


def test_aggregating_query_is_refused():
    merge_sql = MERGE.replace("SELECT x.INDIVIDUAL_ID", "SELECT DISTINCT x.INDIVIDUAL_ID")
    merge, tables, streams = _setup(merge_sql)
    query, why = _source_query(merge, tables, streams, "UPDATED_AT")
    assert query is None and "aggregates" in why


def test_query_mode_cannot_time_travel():
    with pytest.raises(ValidationError):
        BronzeToSilverParityConfig.model_validate({
            "bronzeObject": "A", "silverObject": "B", "bronzeQuery": "SELECT 1",
            "asOfTimestamp": "2026-09-30 00:00:00",
            "keyColumns": [{"name": "K", "bronze": "K", "silver": "K"}],
        })


def test_repair_does_not_look_for_query_columns_on_the_driving_table():
    # Otherwise every query-sourced check's compile error reads as "INDIVIDUAL_ID
    # is missing from POINTS" - the pipeline blamed for our query.
    needed = referenced_columns("BRONZE_TO_SILVER_PARITY", {
        "bronzeObject": "DB.SILVER.POINTS", "silverObject": "DB.GOLD.DAILY",
        "bronzeQuery": "SELECT ...", "bronzeLoadedAtColumn": SOURCE_QUERY_SETTLE_COLUMN,
        "keyColumns": [{"name": "INDIVIDUAL_ID", "bronze": "INDIVIDUAL_ID", "silver": "INDIVIDUAL_ID"}],
    })
    assert needed["DB.SILVER.POINTS"] == set()
    assert needed["DB.GOLD.DAILY"] == {"INDIVIDUAL_ID"}


class _OneRow:
    def __init__(self, row):
        self.row = row

    def run_query(self, sql):
        return [self.row]


def test_parity_that_compared_nothing_is_not_monitored_not_green():
    row = {
        "BRONZE_ROWS_SETTLED": 0, "BRONZE_DISTINCT_KEYS": 0, "SILVER_ROWS": 324,
        "SILVER_DISTINCT_KEYS": 324, "SILVER_DUPLICATE_KEYS": 0, "SILVER_SURPLUS_ROWS": 0,
        "MISSING_IN_SILVER": 0, "EXTRA_IN_SILVER": 0, "VALUE_MISMATCHES": 0,
        "SILVER_AHEAD_OF_SETTLED": 324,
    }
    config = {
        "bronzeObject": "DB.ID.NORMALIZE_EMAIL", "silverObject": "DB.GOLD.EMAIL",
        "keyColumns": [{"name": "ID", "bronze": "ID", "silver": "ID"}],
    }
    outcome = engine._run_bronze_to_silver_parity(_OneRow(row), config)
    assert outcome.status == "INVALID"
    assert "TRUNCATE" in outcome.message
