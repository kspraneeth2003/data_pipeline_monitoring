"""Drill-down queries: short, and listing exactly what their metric counts.

Equivalence with the check's own counts was verified live (65 of 67 matched;
the two that did not came from a check with a RANDOM() filter, which no two
queries can agree on). What is pinned here is the contract that makes the UI
work - every drill-down names a metric the engine really writes - and the
config details a drill-down must carry over from the check.
"""

from app.checks import engine
from app.checks.drilldown import ROW_CAP, build_drilldowns, try_build_drilldowns
from app.ingest.ddl_parser import parse_merges, parse_tables
from app.ingest.heuristic import _is_nondeterministic, _parity_proposal

PARITY = {
    "bronzeObject": "DB.BRONZE.X_RAW",
    "silverObject": "DB.SILVER.X",
    "bronzeLoadedAtColumn": "LOADED_AT",
    "sourceFilter": "QTY > 0",
    "silverFilter": "IS_CURRENT = TRUE",
    "keyColumns": [{"name": "ID", "bronze": "RAW_PAYLOAD:id::NUMBER", "silver": "ID"}],
    "valueColumns": [{"name": "EMAIL", "bronze": "RAW_PAYLOAD:email::STRING", "silver": "EMAIL"}],
}


class _Rows:
    def __init__(self, row):
        self.row = row

    def run_query(self, sql):
        return [self.row]


def _parity_metrics():
    row = {k: 0 for k in (
        "BRONZE_ROWS_SETTLED", "SILVER_DUPLICATE_KEYS", "SILVER_SURPLUS_ROWS",
        "MISSING_IN_SILVER", "EXTRA_IN_SILVER", "VALUE_MISMATCHES", "SILVER_AHEAD_OF_SETTLED",
    )} | {"BRONZE_DISTINCT_KEYS": 5, "SILVER_ROWS": 5, "SILVER_DISTINCT_KEYS": 5}
    return engine._run_bronze_to_silver_parity(_Rows(row), PARITY).metrics


def test_every_parity_drilldown_names_a_metric_the_engine_writes():
    metrics = _parity_metrics()
    drilldowns = build_drilldowns("BRONZE_TO_SILVER_PARITY", PARITY)
    assert {d.metric for d in drilldowns} == {
        "missingInSilver", "extraInSilver", "silverDuplicateKeys", "valueMismatches",
    }
    assert all(d.metric in metrics for d in drilldowns)


def test_every_scd2_drilldown_names_a_metric_the_engine_writes():
    row = {k: 0 for k in (
        "KEYS_WITH_NO_CURRENT", "KEYS_WITH_MANY_CURRENT", "OVERLAPPING_VERSIONS",
        "GAPPED_VERSIONS", "INVALID_WINDOWS", "CURRENT_NOT_OPEN_ENDED", "OPEN_ENDED_NOT_CURRENT",
    )} | {"TOTAL_ROWS": 3, "TOTAL_KEYS": 2}
    config = {"object": "DB.SILVER.MEMBERS", "naturalKeyColumns": ["MEMBER_ID"]}
    metrics = engine._run_scd2_integrity(_Rows(row), config).metrics
    assert all(d.metric in metrics for d in build_drilldowns("SCD2_INTEGRITY", config))


def test_null_rate_drilldown_names_a_metric_the_engine_writes():
    config = {"object": "DB.S.T", "column": "EMAIL", "maxNullRatio": 0}
    metrics = engine._run_null_rate(_Rows({"TOTAL": 4, "NULLS": 1}), config).metrics
    [drilldown] = build_drilldowns("NULL_RATE", config)
    assert drilldown.metric in metrics and "EMAIL IS NULL" in drilldown.sql


def test_the_checks_filters_and_settling_window_carry_over():
    by_metric = {d.metric: d.sql for d in build_drilldowns("BRONZE_TO_SILVER_PARITY", PARITY)}
    missing = by_metric["missingInSilver"]
    assert "(QTY > 0)" in missing and "(IS_CURRENT = TRUE)" in missing
    assert "DATEADD('second', -300" in missing  # the default 5-minute lag
    # Extras are keys in no source row at all, so there is no cutoff - only
    # the load-time presence the check's settled + pending sets share.
    assert "DATEADD" not in by_metric["extraInSilver"]
    assert "LOADED_AT IS NOT NULL" in by_metric["extraInSilver"]
    assert "IS DISTINCT FROM" in by_metric["valueMismatches"]


def test_drilldowns_stay_short_and_capped():
    for drilldown in build_drilldowns("BRONZE_TO_SILVER_PARITY", PARITY):
        assert len(drilldown.sql.splitlines()) <= 15
        assert drilldown.sql.rstrip().endswith(f"LIMIT {ROW_CAP};")


def test_query_sourced_check_drills_into_its_query():
    config = {**PARITY, "sourceFilter": None, "bronzeQuery": "SELECT x.ID AS ID FROM A a JOIN B x ON x.K = a.K"}
    missing = build_drilldowns("BRONZE_TO_SILVER_PARITY", config)[0].sql
    assert "JOIN B x" in missing and ") q" in missing


def test_types_with_nothing_to_list_offer_nothing():
    assert build_drilldowns("FRESHNESS", {"object": "A", "timestampColumn": "T", "maxAgeMinutes": 5}) == []
    assert try_build_drilldowns("BRONZE_TO_SILVER_PARITY", {"not": "valid"}) == []


def test_a_random_filter_gets_no_parity_check():
    assert _is_nondeterministic("(UNIFORM(0, 1, RANDOM()) = 1)")
    assert not _is_nondeterministic("SEQUENCE_ID > 3")  # a column, not SEQ4()
    tables = {t["fqn"]: t for t in parse_tables(
        "CREATE TABLE A.B.SRC (ID NUMBER, LOADED_AT TIMESTAMP_NTZ);"
        "CREATE TABLE A.B.TGT (ID NUMBER);", "x.sql")}
    merge = parse_merges(
        "MERGE INTO A.B.TGT t USING (SELECT s.ID AS ID FROM A.B.SRC s "
        "WHERE UNIFORM(0, 1, RANDOM()) = 1) src ON t.ID = src.ID "
        "WHEN NOT MATCHED THEN INSERT (ID) VALUES (src.ID);", "gen.sql")[0]
    assert _parity_proposal(merge, tables, {}) is None
