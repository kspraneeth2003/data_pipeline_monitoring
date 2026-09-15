"""Bronze -> Silver parity: the deterministic composite-key comparison.

The point of a B2S check is to prove the silver layer is a faithful, *deduplicated*
projection of append-only bronze. Three independent claims, all asserted in one pass:

1. **Dedup happened**   - silver holds exactly one row per composite key.
2. **Nothing was lost**  - every settled bronze key reached silver.
3. **Nothing was invented** - every silver key traces back to bronze.

...and, because key parity alone is famously blind to a broken column mapping
(a MERGE reading the wrong payload field still produces perfect key parity while
writing NULL into every row), an optional fourth:

4. **Values agree** - per value column, bronze's latest row matches silver's.

Determinism is the whole game here. Four things make the result reproducible:

* **One statement, one snapshot.** Every count below comes from a single SQL
  statement. Snowflake evaluates a statement against a consistent snapshot, so
  bronze and silver are read as of the *same* instant. Counting them in two
  round-trips is the classic source of phantom "missing row" alerts when a
  1-minute MERGE task fires in between.
* **A settling lag, applied symmetrically.** Bronze rows that landed seconds ago
  legitimately have not been merged yet, so `lagMinutes` excludes them and
  in-flight rows are never counted as loss. But the lag has to cut both ways: a
  row that landed 30s ago and *was* already merged would otherwise show up as a
  key silver invented from nothing. `bronze_pending` holds those keys back from
  the extra-in-silver count and reports them separately as
  `silverAheadOfSettled`. Filtering one side only trades false "missing" alerts
  for false "extra" ones.
* **Symmetric normalization.** Bronze is untyped VARIANT, silver is typed. Each
  column carries an explicit expression for *both* sides, so `'1'` vs `1` and
  `'2026-05-02'` vs `DATE` never register as a mismatch.
* **NULL-safe set semantics.** Keys join with `IS NOT DISTINCT FROM` and values
  compare with `IS DISTINCT FROM`, so a NULL key part joins to a NULL key part
  instead of silently dropping the row the way `=` would.

A note on why this is not written as `bronze EXCEPT silver`: `EXCEPT` is a *set*
operator and collapses duplicates before comparing. An empty result therefore
proves nothing about whether silver deduplicated - the exact property we are
here to verify. A FULL OUTER JOIN on the composite key keeps multiplicity
observable on both sides.
"""

from app.checks.config_schemas import BronzeToSilverParityConfig


def _key_alias(index: int) -> str:
    return f"K_{index}"


def _value_alias(index: int) -> str:
    return f"V_{index}"


def _at_clause(as_of: str | None) -> str:
    """Optional Snowflake time-travel pin, for replaying a past run exactly."""
    if not as_of:
        return ""
    return f" AT(TIMESTAMP => '{as_of}'::TIMESTAMP_NTZ)"


def _key_string(aliases: list[str], prefix: str = "") -> str:
    """Human-readable composite key for sample output, NULL-safe."""
    parts = ", ".join(f"COALESCE(TO_VARCHAR({prefix}{a}), '<null>')" for a in aliases)
    return f"CONCAT_WS('|', {parts})"


def _joined_key_string(aliases: list[str]) -> str:
    """Same, but for the FULL OUTER JOIN where a key part may live on either side."""
    parts = ", ".join(f"COALESCE(TO_VARCHAR(b.{a}), TO_VARCHAR(s.{a}), '<null>')" for a in aliases)
    return f"CONCAT_WS('|', {parts})"


def build_parity_sql(config: BronzeToSilverParityConfig) -> str:
    key_aliases = [_key_alias(i) for i in range(len(config.keyColumns))]
    value_aliases = [_value_alias(i) for i in range(len(config.valueColumns))]
    at = _at_clause(config.asOfTimestamp)

    bronze_keys = ",\n      ".join(
        f"{col.bronze} AS {alias}" for col, alias in zip(config.keyColumns, key_aliases)
    )
    silver_keys = ",\n      ".join(
        f"{col.silver} AS {alias}" for col, alias in zip(config.keyColumns, key_aliases)
    )
    bronze_values = "".join(
        f",\n      {col.bronze} AS {alias}" for col, alias in zip(config.valueColumns, value_aliases)
    )
    silver_values = "".join(
        f",\n      {col.silver} AS {alias}" for col, alias in zip(config.valueColumns, value_aliases)
    )

    # Latest-per-key on the bronze side. Bronze is append-only, so the same
    # entity legitimately appears many times; the row silver *should* reflect is
    # the most recent one. A tiebreaker column keeps this total-ordered (and so
    # deterministic) when two rows share a load timestamp.
    # Ordering runs against the *aliased* projections, since the raw column names
    # are not in scope once bronze_all has renamed them.
    bronze_seq = (
        f",\n      {config.bronzeSequenceColumn} AS _SEQ" if config.bronzeSequenceColumn else ""
    )
    order_terms = ["_LOADED_AT DESC"]
    if config.bronzeSequenceColumn:
        order_terms.append("_SEQ DESC")
    order_by = ", ".join(order_terms)
    partition_by = ", ".join(key_aliases)

    join_condition = " AND ".join(f"b.{a} IS NOT DISTINCT FROM s.{a}" for a in key_aliases)
    # Pending rows are only consulted for silver-side keys, so they join to s.
    pending_condition = " AND ".join(f"pnd.{a} IS NOT DISTINCT FROM s.{a}" for a in key_aliases)
    bronze_keys_only = ",\n      ".join(
        f"{col.bronze} AS {alias}" for col, alias in zip(config.keyColumns, key_aliases)
    )

    # Per-column mismatch counters. This is what turns "1190 rows disagree" into
    # "WAREHOUSE_ID disagrees on 1190 rows" - i.e. into something a human can act on.
    per_column_counts = "".join(
        f",\n  COUNT_IF(IN_BOTH AND B_{alias} IS DISTINCT FROM S_{alias}) AS MISMATCH_{alias}"
        for alias in value_aliases
    )
    value_fingerprint_b = (
        f"HASH({', '.join(f'b.{a}' for a in value_aliases)})" if value_aliases else "NULL"
    )
    value_fingerprint_s = (
        f"HASH({', '.join(f's.{a}' for a in value_aliases)})" if value_aliases else "NULL"
    )
    joined_values = "".join(
        f",\n    b.{a} AS B_{a},\n    s.{a} AS S_{a}" for a in value_aliases
    )

    key_str = _key_string(key_aliases)
    sample = config.sampleLimit

    return f"""
WITH bounds AS (
  -- CURRENT_TIMESTAMP() is evaluated once per statement, so every CTE below
  -- shares one cutoff. Bronze rows newer than this are still in flight.
  SELECT DATEADD('second', -{int(config.lagMinutes * 60)}, CURRENT_TIMESTAMP()) AS CUTOFF
),
bronze_all AS (
  SELECT
      {bronze_keys}{bronze_values},
      {config.bronzeLoadedAtColumn} AS _LOADED_AT{bronze_seq}
  FROM {config.bronzeObject}{at}
  WHERE {config.bronzeLoadedAtColumn} <= (SELECT CUTOFF FROM bounds)
),
bronze_latest AS (
  SELECT *, 1 AS _B_PRESENT
  FROM bronze_all
  QUALIFY ROW_NUMBER() OVER (PARTITION BY {partition_by} ORDER BY {order_by}) = 1
),
bronze_pending AS (
  -- Rows that landed after the cutoff. They are excluded from the settled set
  -- above, but the MERGE task may already have written them to silver - so
  -- without this they would read as keys silver invented out of nothing.
  SELECT DISTINCT {bronze_keys_only}, 1 AS _P_PRESENT
  FROM {config.bronzeObject}{at}
  WHERE {config.bronzeLoadedAtColumn} > (SELECT CUTOFF FROM bounds)
),
silver_all AS (
  SELECT
      {silver_keys}{silver_values}
  FROM {config.silverObject}{at}
),
silver_counts AS (
  SELECT {partition_by}, COUNT(*) AS _S_ROWS
  FROM silver_all
  GROUP BY {partition_by}
),
silver_one AS (
  SELECT *, 1 AS _S_PRESENT
  FROM silver_all
  QUALIFY ROW_NUMBER() OVER (PARTITION BY {partition_by} ORDER BY 1) = 1
),
joined AS (
  SELECT
    {_joined_key_string(key_aliases)} AS KEY_STR,
    b._B_PRESENT IS NOT NULL AS IN_BRONZE,
    s._S_PRESENT IS NOT NULL AS IN_SILVER,
    (b._B_PRESENT IS NOT NULL AND s._S_PRESENT IS NOT NULL) AS IN_BOTH,
    pnd._P_PRESENT IS NOT NULL AS IN_PENDING,
    {value_fingerprint_b} AS B_FP,
    {value_fingerprint_s} AS S_FP{joined_values}
  FROM bronze_latest b
  FULL OUTER JOIN silver_one s ON {join_condition}
  LEFT JOIN bronze_pending pnd ON {pending_condition}
)
SELECT
  (SELECT COUNT(*) FROM bronze_all)                          AS BRONZE_ROWS_SETTLED,
  (SELECT COUNT(*) FROM bronze_latest)                       AS BRONZE_DISTINCT_KEYS,
  (SELECT COUNT(*) FROM silver_all)                          AS SILVER_ROWS,
  (SELECT COUNT(*) FROM silver_counts)                       AS SILVER_DISTINCT_KEYS,
  (SELECT COUNT(*) FROM silver_counts WHERE _S_ROWS > 1)     AS SILVER_DUPLICATE_KEYS,
  (SELECT COALESCE(SUM(_S_ROWS - 1), 0) FROM silver_counts WHERE _S_ROWS > 1)
                                                             AS SILVER_SURPLUS_ROWS,
  COUNT_IF(IN_BRONZE AND NOT IN_SILVER)                      AS MISSING_IN_SILVER,
  COUNT_IF(IN_SILVER AND NOT IN_BRONZE AND NOT IN_PENDING)   AS EXTRA_IN_SILVER,
  COUNT_IF(IN_SILVER AND NOT IN_BRONZE AND IN_PENDING)       AS SILVER_AHEAD_OF_SETTLED,
  COUNT_IF(IN_BOTH AND B_FP IS DISTINCT FROM S_FP)           AS VALUE_MISMATCHES,
  ARRAY_SLICE(ARRAY_AGG(CASE WHEN IN_BRONZE AND NOT IN_SILVER THEN KEY_STR END), 0, {sample})
                                                             AS SAMPLE_MISSING,
  ARRAY_SLICE(ARRAY_AGG(CASE WHEN IN_SILVER AND NOT IN_BRONZE AND NOT IN_PENDING THEN KEY_STR END), 0, {sample})
                                                             AS SAMPLE_EXTRA,
  ARRAY_SLICE(ARRAY_AGG(CASE WHEN IN_BOTH AND B_FP IS DISTINCT FROM S_FP THEN KEY_STR END), 0, {sample})
                                                             AS SAMPLE_VALUE_MISMATCH,
  (SELECT ARRAY_SLICE(ARRAY_AGG({key_str}), 0, {sample}) FROM silver_counts WHERE _S_ROWS > 1)
                                                             AS SAMPLE_DUPLICATE_KEYS{per_column_counts}
FROM joined
""".strip()


def value_column_names(config: BronzeToSilverParityConfig) -> dict[str, str]:
    """Maps the generated MISMATCH_V_n metric names back to the logical column
    names, so the engine can report "WAREHOUSE_ID" rather than "V_5"."""
    return {f"MISMATCH_{_value_alias(i)}": col.name for i, col in enumerate(config.valueColumns)}
