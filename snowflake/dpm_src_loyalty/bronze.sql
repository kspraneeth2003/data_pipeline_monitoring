-- DPM_SRC_LOYALTY.BRONZE - raw landing layer for the loyalty source.
--
-- This database exists to exercise the pipeline shapes the CRM/billing/inventory
-- sources do not have. Those three are all the same shape: an append-only
-- VARIANT landing table, a stream, and a MERGE that collapses to entity state.
-- A parity engine that only ever sees that shape is untested against most of
-- what real pipelines do.
--
-- Three things here are deliberately different, and each one breaks a different
-- assumption:
--
-- 1. MEMBERS_RAW is a **MERGE-on-PK** bronze, not append-only. The loader
--    upserts one row per entity keyed on MEMBER_ID, so bronze holds current
--    entity state rather than an event log. The parity test that works on an
--    append-only table (settled event count vs silver rows) is the wrong test
--    here - there is no event history in bronze to count. What holds instead is
--    that the distinct PK sets agree.
--
-- 2. POINTS_SNAPSHOT_RAW has a **composite grain**, (MEMBER_ID, SNAPSHOT_DATE).
--    Parity keyed on MEMBER_ID alone would read every day after the first as a
--    duplicate. A check derived from this table has to carry both key columns.
--
-- 3. CURSOR_STATE is the loader's own record of what it fetched. It makes a
--    source-to-bronze question answerable without holding a second connection
--    to the source API: what the loader says it wrote, against what bronze
--    actually holds.
--
-- The CDC shape (OP of I/U/D) is the other half of point 1. A deleted entity
-- is not absent from bronze - it is present with OP='D'. Silver is expected to
-- exclude it, so an unfiltered parity check between the two fails forever on
-- correct data, and the MERGE's own filter has to be lifted onto the source
-- side of the comparison.

-- Unlike the other four source databases, this one does not exist in the
-- account yet, so it is created here. The others predate this file.
CREATE DATABASE IF NOT EXISTS DPM_SRC_LOYALTY
  COMMENT = 'Loyalty source: CDC entity stream plus a daily point series';

CREATE SCHEMA IF NOT EXISTS DPM_SRC_LOYALTY.BRONZE;

-- Member ids are allocated here rather than by the source, since the
-- generator stands in for the source. A real CDC loader would take the id
-- from the payload.
CREATE SEQUENCE IF NOT EXISTS DPM_SRC_LOYALTY.BRONZE.SEQ_MEMBER_ID START = 1 INCREMENT = 1;

-- ---------------------------------------------------------------------------
-- 1. CDC entity table, written MERGE-on-PK
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW (
  -- Promoted out of the payload by the loader so the MERGE has something to
  -- key on. A landing table written MERGE-on-PK cannot key on a VARIANT path.
  MEMBER_ID NUMBER NOT NULL,
  -- The CDC operation the source emitted: I, U or D. A 'D' row is a delete
  -- that has arrived, not a row that is missing.
  OP STRING NOT NULL,
  -- Source-assigned change sequence. This is the ordering the dedup uses -
  -- not LOADED_AT, which records when we fetched rather than when it changed,
  -- and which ties whenever a page lands several changes at once.
  SEQ_NO NUMBER NOT NULL,
  RAW_PAYLOAD VARIANT,
  ETL_LOADED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
  ETL_UPDATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
) COMMENT = 'Bronze: loyalty members, written MERGE-on-PK (one row per entity, latest CDC event)';

CREATE STREAM IF NOT EXISTS DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW_STREAM
  ON TABLE DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW
  COMMENT = 'Changes to bronze loyalty members. Not APPEND_ONLY: a MERGE-on-PK table updates rows in place, and an append-only stream would not see those updates at all';

-- ---------------------------------------------------------------------------
-- 2. Daily snapshot table, composite grain
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS DPM_SRC_LOYALTY.BRONZE.POINTS_SNAPSHOT_RAW (
  RECORD_ID NUMBER AUTOINCREMENT START 1 INCREMENT 1,
  MEMBER_ID NUMBER NOT NULL,
  -- The day this snapshot describes. The source endpoint is windowed rather
  -- than cumulative, so a row is "member M's totals for day D", and a series
  -- is only reconstructable if the day is stored alongside it.
  SNAPSHOT_DATE DATE NOT NULL,
  RAW_PAYLOAD VARIANT,
  LOADED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
) COMMENT = 'Bronze: daily loyalty point snapshots, append-only, grain (MEMBER_ID, SNAPSHOT_DATE)';

CREATE STREAM IF NOT EXISTS DPM_SRC_LOYALTY.BRONZE.POINTS_SNAPSHOT_RAW_STREAM
  ON TABLE DPM_SRC_LOYALTY.BRONZE.POINTS_SNAPSHOT_RAW
  APPEND_ONLY = TRUE
  COMMENT = 'New daily point snapshots landed in bronze';

-- ---------------------------------------------------------------------------
-- 3. Loader watermark state
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS DPM_SRC_LOYALTY.BRONZE.CURSOR_STATE (
  TABLE_NAME STRING NOT NULL,
  LAST_CURSOR STRING,
  PAGES_LOADED NUMBER,
  -- What the loader believes it wrote. Comparing this against what bronze
  -- holds is the closest thing to a source-to-bronze parity check that is
  -- answerable from inside the warehouse: a loader that reports 40,000 rows
  -- fetched into a table holding 31,000 has lost 9,000 somewhere between the
  -- API response and the INSERT, and nothing downstream can tell.
  ROWS_LOADED NUMBER,
  STATUS STRING,
  UPDATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
) COMMENT = 'Bronze: per-table loader watermark and row accounting';

-- ---------------------------------------------------------------------------
-- Bronze -> Silver: the SCD2 build
-- ---------------------------------------------------------------------------
--
-- Two statements rather than one MERGE, because SCD2 needs both: close the
-- superseded row, then open the new one. A single MERGE cannot do both to the
-- same natural key in one pass - Snowflake rejects a MERGE that matches the
-- same target row twice, which is exactly what a key with two changes in one
-- batch would do.
--
-- The source filter is `OP <> 'D'`. Deleted members stay in bronze as tombstones
-- and must not become current rows in silver. Any parity check between these
-- two tables has to apply the same predicate to the bronze side, or it will
-- report every deleted member as "missing in silver" forever.

CREATE OR REPLACE TASK DPM_SRC_LOYALTY.BRONZE.TASK_BRONZE_TO_SILVER_MEMBERS_CLOSE
  WAREHOUSE = DPM_PIPELINE_WH
  SCHEDULE = '1 MINUTE'
  WHEN SYSTEM$STREAM_HAS_DATA('DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW_STREAM')
AS
MERGE INTO DPM_SRC_LOYALTY.SILVER.MEMBERS tgt
USING (
  SELECT
    s.MEMBER_ID AS MEMBER_ID,
    s.RAW_PAYLOAD:tier::STRING AS TIER,
    s.RAW_PAYLOAD:status::STRING AS STATUS,
    s.ETL_LOADED_AT AS CHANGED_AT
  FROM DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW_STREAM s
  WHERE s.MEMBER_ID IS NOT NULL
  -- One row per member per batch: the newest change wins. PARTITION BY names
  -- the natural key and ORDER BY names the sequence, which together are the
  -- whole dedup contract.
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY s.MEMBER_ID
    ORDER BY s.SEQ_NO DESC
  ) = 1
) src
  ON tgt.MEMBER_ID IS NOT DISTINCT FROM src.MEMBER_ID
 AND tgt.IS_CURRENT = TRUE
WHEN MATCHED
 AND (tgt.TIER IS DISTINCT FROM src.TIER OR tgt.STATUS IS DISTINCT FROM src.STATUS)
THEN UPDATE SET
  VALID_TO = src.CHANGED_AT,
  IS_CURRENT = FALSE,
  UPDATED_AT = CURRENT_TIMESTAMP();

CREATE OR REPLACE TASK DPM_SRC_LOYALTY.BRONZE.TASK_BRONZE_TO_SILVER_MEMBERS_OPEN
  WAREHOUSE = DPM_PIPELINE_WH
  AFTER DPM_SRC_LOYALTY.BRONZE.TASK_BRONZE_TO_SILVER_MEMBERS_CLOSE
AS
MERGE INTO DPM_SRC_LOYALTY.SILVER.MEMBERS tgt
USING (
  SELECT
    b.MEMBER_ID AS MEMBER_ID,
    b.RAW_PAYLOAD:full_name::STRING AS FULL_NAME,
    b.RAW_PAYLOAD:email::STRING AS EMAIL,
    b.RAW_PAYLOAD:tier::STRING AS TIER,
    b.RAW_PAYLOAD:status::STRING AS STATUS,
    b.RAW_PAYLOAD:enrolled_date::DATE AS ENROLLED_DATE,
    b.ETL_LOADED_AT AS CHANGED_AT
  FROM DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW b
  -- The lifted filter. A tombstone is a real bronze row; it is simply not a
  -- member silver should carry as current.
  WHERE b.OP <> 'D'
    AND b.MEMBER_ID IS NOT NULL
) src
  ON tgt.MEMBER_ID IS NOT DISTINCT FROM src.MEMBER_ID
 AND tgt.IS_CURRENT = TRUE
WHEN NOT MATCHED THEN INSERT (
  MEMBER_ID, FULL_NAME, EMAIL, TIER, STATUS, ENROLLED_DATE,
  VALID_FROM, VALID_TO, IS_CURRENT, UPDATED_AT
) VALUES (
  src.MEMBER_ID, src.FULL_NAME, src.EMAIL, src.TIER, src.STATUS, src.ENROLLED_DATE,
  src.CHANGED_AT, '9999-12-31'::TIMESTAMP_NTZ, TRUE, CURRENT_TIMESTAMP()
);

-- ---------------------------------------------------------------------------
-- Bronze -> Silver: the daily point series
-- ---------------------------------------------------------------------------
--
-- Insert-only, not a MERGE. The grain is (member, day) and a day's row never
-- changes once captured, so an insert is idempotent against the stream and a
-- MERGE would only add a rewrite cost for no behaviour.

CREATE OR REPLACE TASK DPM_SRC_LOYALTY.BRONZE.TASK_BRONZE_TO_SILVER_POINTS_DAILY
  WAREHOUSE = DPM_PIPELINE_WH
  SCHEDULE = '1 MINUTE'
  WHEN SYSTEM$STREAM_HAS_DATA('DPM_SRC_LOYALTY.BRONZE.POINTS_SNAPSHOT_RAW_STREAM')
AS
MERGE INTO DPM_SRC_LOYALTY.SILVER.POINTS_DAILY tgt
USING (
  SELECT
    s.MEMBER_ID AS MEMBER_ID,
    s.SNAPSHOT_DATE AS SNAPSHOT_DATE,
    s.RAW_PAYLOAD:points_earned::NUMBER AS POINTS_EARNED,
    s.RAW_PAYLOAD:points_redeemed::NUMBER AS POINTS_REDEEMED,
    s.RAW_PAYLOAD:channel::STRING AS CHANNEL
  FROM DPM_SRC_LOYALTY.BRONZE.POINTS_SNAPSHOT_RAW_STREAM s
  WHERE METADATA$ACTION = 'INSERT'
    AND s.MEMBER_ID IS NOT NULL
    AND s.SNAPSHOT_DATE IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY s.MEMBER_ID, s.SNAPSHOT_DATE
    ORDER BY s.RECORD_ID DESC
  ) = 1
) src
  -- Both key columns. Keying on MEMBER_ID alone would collapse the series to
  -- one row per member, which is the bug this table's grain exists to avoid.
  ON tgt.MEMBER_ID = src.MEMBER_ID
 AND tgt.SNAPSHOT_DATE = src.SNAPSHOT_DATE
WHEN NOT MATCHED THEN INSERT (
  MEMBER_ID, SNAPSHOT_DATE, POINTS_EARNED, POINTS_REDEEMED, CHANNEL, UPDATED_AT
) VALUES (
  src.MEMBER_ID, src.SNAPSHOT_DATE, src.POINTS_EARNED, src.POINTS_REDEEMED, src.CHANNEL, CURRENT_TIMESTAMP()
);
