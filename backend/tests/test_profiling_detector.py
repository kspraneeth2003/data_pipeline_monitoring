"""The detector's judgement calls, each pinned by the case that motivated it.

The failure worth fearing here is not a missed anomaly - it is a noisy one.
A detector that fires on a 0.01% wobble, on an append-only table growing, or
on a column that has always been NULL by design gets muted, and a muted
detector misses the real fault next to the noise. So half of these assert
that something is *not* reported.
"""

from app.profiling.detector import MIN_HISTORY, ColumnMetrics, Snapshot, detect
from app.profiling.sql import NUMERIC, TEMPORAL, TEXT

NOW = 1_790_000_000.0


def col(name="C", family=NUMERIC, rows=1000, nulls=0, **kw) -> ColumnMetrics:
    data_type = {"NUMERIC": "NUMBER", "TEXT": "TEXT", "TEMPORAL": "TIMESTAMP_NTZ"}.get(family, family)
    return ColumnMetrics(name=name, data_type=data_type, family=family, row_count=rows, null_count=nulls, **kw)


def snap(rows=1000, *cols: ColumnMetrics) -> Snapshot:
    return Snapshot(row_count=rows, columns={c.name: c for c in cols})


def steady(n=MIN_HISTORY + 2, rows=1000, **col_kw) -> list[Snapshot]:
    return [snap(rows, col(rows=rows, **col_kw)) for _ in range(n)]


def metrics(drafts):
    return {(d.column_name, d.metric) for d in drafts}


# --- Findings: no history needed -------------------------------------------


def test_all_null_column_is_found_on_the_first_run():
    # The WAREHOUSE_ID shape: NULL on every row since the first load. A
    # history-only detector would learn that as normal.
    current = snap(3462, col("WAREHOUSE_ID", TEXT, rows=3462, nulls=3462))
    drafts = detect(current, [], NOW)
    assert ("WAREHOUSE_ID", "all_null") in metrics(drafts)
    assert drafts[0].kind == "FINDING"


def test_all_null_stays_a_finding_when_it_has_always_been_null():
    history = [snap(3462, col("WAREHOUSE_ID", TEXT, rows=3462, nulls=3462)) for _ in range(10)]
    current = snap(3462, col("WAREHOUSE_ID", TEXT, rows=3462, nulls=3462))
    found = metrics(detect(current, history, NOW))
    assert ("WAREHOUSE_ID", "all_null") in found
    assert ("WAREHOUSE_ID", "null_ratio") not in found


def test_a_column_that_becomes_all_null_is_a_high_anomaly_not_a_finding():
    history = steady(nulls=5)
    current = snap(1000, col(rows=1000, nulls=1000))
    drafts = detect(current, history, NOW)
    [d] = [d for d in drafts if d.column_name == "C"]
    assert (d.kind, d.metric, d.severity) == ("ANOMALY", "null_ratio", "HIGH")


def test_future_timestamp_is_found():
    current = snap(10, col("TS", TEMPORAL, rows=10, max_numeric=NOW + 30 * 86400))
    assert ("TS", "future_timestamp") in metrics(detect(current, [], NOW))


def test_empty_table_is_a_finding_without_history():
    assert ("empty_table" in {d.metric for d in detect(snap(0), [], NOW)})


# --- Baseline -----------------------------------------------------------------


def test_nothing_is_judged_against_history_before_a_baseline_exists():
    history = steady(n=MIN_HISTORY - 1, nulls=0)
    current = snap(1000, col(rows=1000, nulls=900))
    assert detect(current, history, NOW) == []


def test_a_table_that_empties_is_reported_even_before_a_baseline():
    history = steady(n=2)
    drafts = detect(snap(0, col(rows=0)), history, NOW)
    assert [(d.metric, d.severity) for d in drafts] == [("row_count", "HIGH")]


# --- Null rate --------------------------------------------------------------


def test_null_rate_jump_is_an_anomaly():
    history = steady(nulls=1)  # 0.1% null
    current = snap(1000, col(rows=1000, nulls=400))
    [d] = detect(current, history, NOW)
    assert (d.metric, d.severity) == ("null_ratio", "HIGH")
    assert "40.0% null" in d.message


def test_small_null_wobble_on_a_flat_series_is_not_reported():
    # MAD is zero on a perfectly flat history; without a floor, 0.5% would fire.
    history = steady(nulls=0)
    assert detect(snap(1000, col(rows=1000, nulls=5)), history, NOW) == []


def test_one_bad_run_in_history_does_not_widen_the_band():
    history = steady(nulls=1)
    history[3] = snap(1000, col(rows=1000, nulls=900))
    [d] = detect(snap(1000, col(rows=1000, nulls=400)), history, NOW)
    assert d.metric == "null_ratio"


# --- Row volume -------------------------------------------------------------


def test_steady_growth_is_not_an_anomaly():
    history = [snap(1000 + 500 * i, col(rows=1000 + 500 * i)) for i in range(10)]
    current = snap(1000 + 500 * 10, col(rows=1000 + 500 * 10))
    assert detect(current, history, NOW) == []


def test_a_collapsed_load_is_an_anomaly():
    history = [snap(1000 + 500 * i, col(rows=1000 + 500 * i)) for i in range(10)]
    current = snap(5500 + 20, col(rows=5500 + 20))
    assert "row_volume" in {d.metric for d in detect(current, history, NOW)}


def test_rows_disappearing_from_a_growing_table_is_high():
    history = [snap(1000 + 500 * i, col(rows=1000 + 500 * i)) for i in range(10)]
    current = snap(2000, col(rows=2000))
    [d] = [d for d in detect(current, history, NOW) if d.metric == "row_volume"]
    assert d.severity == "HIGH"
    assert "only ever grown" in d.message


# --- Values -----------------------------------------------------------------


def test_first_negative_value_is_high():
    history = steady(min_numeric=0.0, max_numeric=500.0, mean_numeric=100.0)
    current = snap(1000, col(rows=1000, min_numeric=-20.0, max_numeric=500.0, mean_numeric=100.0))
    [d] = detect(current, history, NOW)
    assert (d.metric, d.severity) == ("min", "HIGH")


def test_max_far_beyond_history_is_reported():
    history = steady(min_numeric=1.0, max_numeric=100.0, mean_numeric=50.0)
    current = snap(1000, col(rows=1000, min_numeric=1.0, max_numeric=10_000.0, mean_numeric=50.0))
    assert ("C", "max") in metrics(detect(current, history, NOW))


def test_mean_shift_is_reported_and_small_drift_is_not():
    history = steady(min_numeric=1.0, max_numeric=100.0, mean_numeric=50.0)
    shifted = snap(1000, col(rows=1000, min_numeric=1.0, max_numeric=100.0, mean_numeric=80.0))
    drifted = snap(1000, col(rows=1000, min_numeric=1.0, max_numeric=100.0, mean_numeric=52.0))
    assert ("C", "mean") in metrics(detect(shifted, history, NOW))
    assert detect(drifted, history, NOW) == []


def test_key_that_stops_being_unique_is_reported():
    history = steady(distinct_count=1000)
    current = snap(1000, col(rows=1000, distinct_count=700))
    [d] = detect(current, history, NOW)
    assert d.metric == "distinct_ratio"
    assert "stopped being unique" in d.message


def test_blank_strings_are_reported():
    history = steady(family=TEXT, blank_count=0, mean_numeric=10.0)
    current = snap(1000, col(family=TEXT, rows=1000, blank_count=300, mean_numeric=10.0))
    assert ("C", "blank_ratio") in metrics(detect(current, history, NOW))


def test_latest_timestamp_going_backwards_is_reported():
    history = [snap(1000, col("TS", TEMPORAL, max_numeric=NOW - 3600 * (10 - i))) for i in range(10)]
    current = snap(1000, col("TS", TEMPORAL, max_numeric=NOW - 3600 * 20))
    assert ("TS", "max_went_backwards") in metrics(detect(current, history, NOW))


# --- Schema -----------------------------------------------------------------


def test_schema_changes_against_the_previous_run():
    previous = snap(1000, col("A"), col("B"))
    current = snap(1000, col("A", TEXT), col("C"))
    found = {(d.column_name, d.metric, d.severity) for d in detect(current, [previous], NOW)}
    assert ("C", "column_added", "LOW") in found
    assert ("B", "column_removed", "HIGH") in found
    assert ("A", "type_changed", "MEDIUM") in found
