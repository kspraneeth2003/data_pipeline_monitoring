"""SCD2 integrity: does the history a type-2 dimension holds make sense.

This is the check that exists because parity cannot see the problem. Parity asks
whether the rows arrived; a dimension can pass it completely - every source key
present, every value matching - and still be unusable, because the *versions*
those rows form contradict each other. Two rows both claiming to be current, a
validity window that overlaps the next one, a gap where a member existed but no
version covers the date: each of these is invisible to any row-for-row
comparison, and each silently corrupts every as-of join made against the table.

Four assertions, evaluated in one pass but counted separately, because they fail
differently and each points somewhere else:

  1. **Exactly one current row per key.** Zero means the open step did not run,
     or closed a row it should not have. More than one means the close step did
     not run, and every join to the dimension now fans out and doubles measures.
  2. **No overlapping windows.** Two versions covering the same instant means an
     as-of lookup returns two rows for one key at one time - the query is not
     wrong, the data is ambiguous.
  3. **No gaps between versions.** A gap is a lost batch: the key existed
     throughout, but no version covers the interval, so an as-of join returns
     nothing and the fact silently drops out of the result.
  4. **Well-formed windows.** VALID_FROM before VALID_TO on every row, and the
     current row carrying the agreed open-ended marker. Half-sentinel,
     half-NULL is how this rots in practice, and it rots quietly: every
     comparison that forgets the COALESCE reads an open version as a
     zero-length one.

Why counted rather than sampled into a pass/fail on the first offender: the
number is the diagnosis. One overlapping key after a deploy is a bad batch; four
thousand is the close step having been broken for a week, and the two want
different responses.

The statement is a single query so the four counts describe the same snapshot of
the table. Running them as four queries would let the dimension change between
them, and a report that mixes two states is worse than a slower one.
"""

from app.checks.config_schemas import Scd2IntegrityConfig


def _key_expr(config: Scd2IntegrityConfig) -> str:
    """The natural key, as one comparable expression.

    Concatenated with a separator rather than compared column-by-column so the
    window functions below partition on one value. `NVL` is not optional: a null
    in any part would otherwise make the whole key null, and every row with a
    null key would collapse into a single partition and report as overlapping
    with all the others.
    """
    parts = [f"NVL(TO_VARCHAR({column}), '\\x00')" for column in config.naturalKeyColumns]
    return " || '|' || ".join(parts)


def _open_ended_predicate(config: Scd2IntegrityConfig) -> str:
    """How a row says "this version is still open".

    Two conventions are in use and the check has to be told which, because they
    are not interchangeable and a table that mixes them is precisely the fault
    being looked for. Asking would be answerable from the data only by guessing
    at whichever is more common in it - which would make the check agree with
    the corruption it exists to find.
    """
    if config.openEndedSentinel is None:
        return f"{config.validToColumn} IS NULL"
    return f"{config.validToColumn} = '{config.openEndedSentinel}'::{config.validToType}"


def build_scd2_sql(config: Scd2IntegrityConfig) -> str:
    key = _key_expr(config)
    vf = config.validFromColumn
    vt = config.validToColumn
    cur = config.currentFlagColumn
    open_ended = _open_ended_predicate(config)
    sample = config.sampleLimit

    # A closed window's end is its own VALID_TO. An open one has no end, so for
    # ordering purposes it is treated as the far future - otherwise a NULL
    # sentinel sorts as unknown and every comparison against it is NULL, which
    # reads as "no violation" and makes the check silently vacuous.
    effective_end = (
        f"COALESCE({vt}, '9999-12-31'::TIMESTAMP_NTZ)"
        if config.openEndedSentinel is None
        else vt
    )

    return f"""
WITH versions AS (
  SELECT
      {key} AS K,
      {vf} AS VF,
      {effective_end} AS VT,
      {cur} AS IS_CUR,
      ({open_ended}) AS IS_OPEN_ENDED
  FROM {config.object}
),
ordered AS (
  SELECT
      K, VF, VT, IS_CUR, IS_OPEN_ENDED,
      -- The next version of the same key, by start time. Comparing each row
      -- against its own successor is what turns "the history is consistent"
      -- into something countable.
      LEAD(VF) OVER (PARTITION BY K ORDER BY VF, VT) AS NEXT_VF
  FROM versions
),
per_key AS (
  SELECT K, COUNT_IF(IS_CUR) AS N_CURRENT, COUNT(*) AS N_VERSIONS
  FROM versions
  GROUP BY K
)
SELECT
  (SELECT COUNT(*) FROM versions)                                   AS TOTAL_ROWS,
  (SELECT COUNT(*) FROM per_key)                                    AS TOTAL_KEYS,

  -- 1. exactly one current row per key, split so "none" and "several" are
  --    distinguishable - they are opposite faults with opposite fixes.
  (SELECT COUNT(*) FROM per_key WHERE N_CURRENT = 0)                AS KEYS_WITH_NO_CURRENT,
  (SELECT COUNT(*) FROM per_key WHERE N_CURRENT > 1)                AS KEYS_WITH_MANY_CURRENT,

  -- 2. the next version starts before this one ends
  (SELECT COUNT(*) FROM ordered
    WHERE NEXT_VF IS NOT NULL AND NEXT_VF < VT)                     AS OVERLAPPING_VERSIONS,

  -- 3. the next version starts after this one ends
  (SELECT COUNT(*) FROM ordered
    WHERE NEXT_VF IS NOT NULL AND NEXT_VF > VT)                     AS GAPPED_VERSIONS,

  -- 4a. a window that ends before it starts
  (SELECT COUNT(*) FROM versions WHERE VF >= VT)                    AS INVALID_WINDOWS,
  -- 4b. the current row must be the open-ended one, and only it
  (SELECT COUNT(*) FROM versions WHERE IS_CUR AND NOT IS_OPEN_ENDED) AS CURRENT_NOT_OPEN_ENDED,
  (SELECT COUNT(*) FROM versions WHERE IS_OPEN_ENDED AND NOT IS_CUR) AS OPEN_ENDED_NOT_CURRENT,

  -- Samples, so a non-zero count is actionable without writing a second query.
  (SELECT ARRAY_AGG(K) FROM (
      SELECT K FROM per_key WHERE N_CURRENT <> 1 LIMIT {sample}
   ))                                                               AS SAMPLE_CURRENT_FLAG_KEYS,
  (SELECT ARRAY_AGG(K) FROM (
      SELECT DISTINCT K FROM ordered
       WHERE NEXT_VF IS NOT NULL AND NEXT_VF < VT LIMIT {sample}
   ))                                                               AS SAMPLE_OVERLAPPING_KEYS,
  (SELECT ARRAY_AGG(K) FROM (
      SELECT DISTINCT K FROM ordered
       WHERE NEXT_VF IS NOT NULL AND NEXT_VF > VT LIMIT {sample}
   ))                                                               AS SAMPLE_GAPPED_KEYS
""".strip()
