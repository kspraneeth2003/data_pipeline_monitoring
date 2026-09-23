-- DPM_LOYALTY_360.GOLD - the loyalty serving layer.
--
-- This database is where silver -> gold stops being a projection and becomes an
-- aggregate, which matters because it is a different check.
--
-- CUSTOMER_360 (the other gold table in this pipeline) is row-for-row: one gold
-- row per silver customer, so key parity is meaningful and a missing key is a
-- real finding. MEMBER_ENGAGEMENT is not. It rolls a daily series up to one row
-- per member, so "every silver key appears in gold" is trivially false by
-- design - there are 90 silver rows behind one gold row.
--
-- What holds instead is reconciliation: the totals agree at a stated grain.
-- SUM(POINTS_EARNED) over the silver series, per member, equals
-- TOTAL_POINTS_EARNED in gold. That is the only statement about this hop that
-- is both true and worth making, and a parity check bent to fit it would have
-- to be so loosened it would stop catching anything.
--
-- The other thing this table is for is referential integrity. Every MEMBER_ID
-- in gold must resolve to a member in the silver dimension - and because that
-- dimension is SCD2, "resolve" has to mean the *current* version, or a member
-- with three historical versions resolves three times and the join fans out.

CREATE DATABASE IF NOT EXISTS DPM_LOYALTY_360
  COMMENT = 'Loyalty serving layer';

CREATE SCHEMA IF NOT EXISTS DPM_LOYALTY_360.GOLD;

CREATE TABLE IF NOT EXISTS DPM_LOYALTY_360.GOLD.MEMBER_ENGAGEMENT (
  MEMBER_ID NUMBER,
  FULL_NAME STRING,
  TIER STRING,
  STATUS STRING,
  ENROLLED_DATE DATE,
  -- The reconciled measures. Each one has a matching aggregate over
  -- SILVER.POINTS_DAILY, which is what makes this table checkable at all.
  ACTIVE_DAYS NUMBER,
  TOTAL_POINTS_EARNED NUMBER,
  TOTAL_POINTS_REDEEMED NUMBER,
  NET_POINTS NUMBER,
  LAST_ACTIVITY_DATE DATE,
  UPDATED_AT TIMESTAMP_NTZ
) COMMENT = 'Gold: one row per loyalty member, with their point series rolled up';

CREATE OR REPLACE TASK DPM_LOYALTY_360.GOLD.TASK_SILVER_TO_GOLD_MEMBER_ENGAGEMENT
  WAREHOUSE = DPM_PIPELINE_WH
  SCHEDULE = '1 MINUTE'
  WHEN SYSTEM$STREAM_HAS_DATA('DPM_SRC_LOYALTY.SILVER.MEMBERS_STREAM')
    OR SYSTEM$STREAM_HAS_DATA('DPM_SRC_LOYALTY.SILVER.POINTS_DAILY_STREAM')
AS
MERGE INTO DPM_LOYALTY_360.GOLD.MEMBER_ENGAGEMENT tgt
USING (
  WITH changed_members AS (
    SELECT MEMBER_ID FROM DPM_SRC_LOYALTY.SILVER.MEMBERS_STREAM
    UNION
    SELECT MEMBER_ID FROM DPM_SRC_LOYALTY.SILVER.POINTS_DAILY_STREAM
  )
  SELECT
    m.MEMBER_ID,
    m.FULL_NAME,
    m.TIER,
    m.STATUS,
    m.ENROLLED_DATE,
    COUNT(p.SNAPSHOT_DATE) AS ACTIVE_DAYS,
    COALESCE(SUM(p.POINTS_EARNED), 0) AS TOTAL_POINTS_EARNED,
    COALESCE(SUM(p.POINTS_REDEEMED), 0) AS TOTAL_POINTS_REDEEMED,
    COALESCE(SUM(p.POINTS_EARNED), 0) - COALESCE(SUM(p.POINTS_REDEEMED), 0) AS NET_POINTS,
    MAX(p.SNAPSHOT_DATE) AS LAST_ACTIVITY_DATE
  FROM DPM_SRC_LOYALTY.SILVER.MEMBERS m
  LEFT JOIN DPM_SRC_LOYALTY.SILVER.POINTS_DAILY p
    ON p.MEMBER_ID = m.MEMBER_ID
  -- Current versions only. Without this the dimension's own history fans the
  -- join out and every measure is multiplied by the number of versions the
  -- member has - which looks like a loyalty programme doing very well.
  WHERE m.IS_CURRENT = TRUE
    AND m.MEMBER_ID IN (SELECT MEMBER_ID FROM changed_members)
  GROUP BY m.MEMBER_ID, m.FULL_NAME, m.TIER, m.STATUS, m.ENROLLED_DATE
) src
ON tgt.MEMBER_ID = src.MEMBER_ID
WHEN MATCHED THEN UPDATE SET
  FULL_NAME = src.FULL_NAME,
  TIER = src.TIER,
  STATUS = src.STATUS,
  ENROLLED_DATE = src.ENROLLED_DATE,
  ACTIVE_DAYS = src.ACTIVE_DAYS,
  TOTAL_POINTS_EARNED = src.TOTAL_POINTS_EARNED,
  TOTAL_POINTS_REDEEMED = src.TOTAL_POINTS_REDEEMED,
  NET_POINTS = src.NET_POINTS,
  LAST_ACTIVITY_DATE = src.LAST_ACTIVITY_DATE,
  UPDATED_AT = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (
  MEMBER_ID, FULL_NAME, TIER, STATUS, ENROLLED_DATE,
  ACTIVE_DAYS, TOTAL_POINTS_EARNED, TOTAL_POINTS_REDEEMED, NET_POINTS,
  LAST_ACTIVITY_DATE, UPDATED_AT
) VALUES (
  src.MEMBER_ID, src.FULL_NAME, src.TIER, src.STATUS, src.ENROLLED_DATE,
  src.ACTIVE_DAYS, src.TOTAL_POINTS_EARNED, src.TOTAL_POINTS_REDEEMED, src.NET_POINTS,
  src.LAST_ACTIVITY_DATE, CURRENT_TIMESTAMP()
);
