-- DPM_SRC_LOYALTY.SILVER - the conformed loyalty layer.
--
-- MEMBERS is a slowly-changing dimension, type 2: one row per member per
-- version, with a validity window and a current flag. It is here because SCD2
-- is where a pipeline can be perfectly lossless and still wrong - every bronze
-- row accounted for, every key present, and the history still unusable because
-- two versions of a member both claim to be current, or their windows overlap,
-- or there is a gap between one version closing and the next opening.
--
-- None of those are visible to a parity check. Parity asks "did the rows
-- arrive"; SCD2 integrity asks "does the history they form make sense". Both
-- are needed and neither substitutes for the other.
--
-- The four assertions this table is built to be checked against:
--
--   1. exactly one IS_CURRENT row per MEMBER_ID
--   2. no two versions of a MEMBER_ID with overlapping [VALID_FROM, VALID_TO)
--   3. no gap between one version's VALID_TO and the next's VALID_FROM
--   4. VALID_FROM < VALID_TO on every row, and the current row's VALID_TO is
--      the sentinel 9999-12-31
--
-- They are four checks and not one because they fail differently and point at
-- different bugs: (1) is the close step not running, (2) is it running against
-- the wrong version, (3) is a lost batch, (4) is clock skew or a bad default.

CREATE SCHEMA IF NOT EXISTS DPM_SRC_LOYALTY.SILVER;

CREATE TABLE IF NOT EXISTS DPM_SRC_LOYALTY.SILVER.MEMBERS (
  -- Surrogate key: unique per *version*, so a fact can pin the version of the
  -- member it was true of. MEMBER_ID alone cannot do that, since it repeats
  -- once per version by design.
  MEMBER_KEY NUMBER AUTOINCREMENT START 1 INCREMENT 1,
  -- Natural key: the source's own identifier, repeated across versions.
  MEMBER_ID NUMBER NOT NULL,
  FULL_NAME STRING,
  EMAIL STRING,
  -- A closed domain. Four values are legal; a fifth means the source added a
  -- tier nobody downstream knows how to price.
  TIER STRING,
  -- Also closed: ACTIVE, LAPSED, SUSPENDED.
  STATUS STRING,
  ENROLLED_DATE DATE,
  VALID_FROM TIMESTAMP_NTZ NOT NULL,
  -- The sentinel, not NULL, for the open row. Chosen because a NULL end date
  -- makes every window comparison need a COALESCE, and one query that forgets
  -- it reads an open version as a zero-length one. The consistency is itself
  -- worth checking - half-sentinel, half-NULL is the common way this rots.
  VALID_TO TIMESTAMP_NTZ NOT NULL,
  IS_CURRENT BOOLEAN NOT NULL,
  UPDATED_AT TIMESTAMP_NTZ
) COMMENT = 'Silver: loyalty members as an SCD2 dimension (one row per member version)';

CREATE STREAM IF NOT EXISTS DPM_SRC_LOYALTY.SILVER.MEMBERS_STREAM
  ON TABLE DPM_SRC_LOYALTY.SILVER.MEMBERS
  COMMENT = 'Changes to the silver member dimension, for the gold refresh';

-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS DPM_SRC_LOYALTY.SILVER.POINTS_DAILY (
  MEMBER_ID NUMBER NOT NULL,
  SNAPSHOT_DATE DATE NOT NULL,
  -- Non-negative by contract. A negative earned figure has always meant a
  -- reversal was written to the wrong column, never a real event.
  POINTS_EARNED NUMBER,
  POINTS_REDEEMED NUMBER,
  -- Closed domain: APP, WEB, STORE, PARTNER.
  CHANNEL STRING,
  UPDATED_AT TIMESTAMP_NTZ
) COMMENT = 'Silver: daily loyalty point series, grain (MEMBER_ID, SNAPSHOT_DATE)';

CREATE STREAM IF NOT EXISTS DPM_SRC_LOYALTY.SILVER.POINTS_DAILY_STREAM
  ON TABLE DPM_SRC_LOYALTY.SILVER.POINTS_DAILY
  COMMENT = 'New rows in the daily point series, for the gold refresh';
