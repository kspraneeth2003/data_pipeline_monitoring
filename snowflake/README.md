# The test pipeline, as reviewed intent

This directory is what the pipeline is *supposed* to be: idempotent, reviewed
DDL, tracked in git so the RCA agent can attribute a failing object to a commit
and an author. `../../ioi/` is the other half — a `GET_DDL` mirror of what the
account actually contains. They are expected to drift, and the drift is the
interesting part.

## What each database is for

| Database | Shape it exercises |
|---|---|
| `DPM_SRC_CRM` | append-only VARIANT landing → MERGE dedup into typed silver |
| `DPM_SRC_BILLING` | the same, plus a closed-domain `STATUS` column and a money column |
| `DPM_SRC_INVENTORY` | the same, plus a **filtered** MERGE and the standing mis-mapped-payload bug |
| `DPM_CUSTOMER_360` | silver → gold as a row-for-row projection, so key parity is meaningful |
| `DPM_INVENTORY_360` | silver → gold aggregate |
| `DPM_SRC_LOYALTY` | **CDC / MERGE-on-PK bronze**, composite snapshot grain, **SCD2 dimension**, loader cursor state |
| `DPM_LOYALTY_360` | aggregate gold where key parity is meaningless and reconciliation is the check |

The first five are all one shape. `DPM_SRC_LOYALTY` exists because a parity
engine tested only against that shape is untested against most of what real
pipelines do — the header comment on `dpm_src_loyalty/bronze.sql` says what each
of its three tables breaks and why.

## Deploying

The loyalty databases do not exist in the account yet. Everything here is
idempotent (`CREATE ... IF NOT EXISTS`, `CREATE OR REPLACE TASK`), so a re-run is
safe for the tables — but note that `CREATE OR REPLACE TASK` resets a task's
state, and `CREATE OR REPLACE STREAM` **discards an unconsumed stream offset**,
which drops whatever that stream had not yet moved. That is fine on a test
account and is not fine anywhere else.

Order matters: tables before streams before tasks, since a stream needs its
table and a task needs its stream.

```sql
-- 1. the source database: tables, streams, then the bronze -> silver tasks
--    (dpm_src_loyalty/silver.sql first, so the MERGE targets exist)
@dpm_src_loyalty/silver.sql
@dpm_src_loyalty/bronze.sql

-- 2. the serving database
@dpm_loyalty_360/gold.sql

-- 3. the generator that stands in for the loyalty API loader
@dpm_loyalty_360/generator.sql

-- 4. prime it, then let the hourly task take over
CALL DPM_LOYALTY_360.GOLD.SP_GENERATE_LOYALTY_DATA(25, 10, 30);
ALTER TASK DPM_SRC_LOYALTY.BRONZE.TASK_BRONZE_TO_SILVER_MEMBERS_OPEN RESUME;
ALTER TASK DPM_SRC_LOYALTY.BRONZE.TASK_BRONZE_TO_SILVER_MEMBERS_CLOSE RESUME;
ALTER TASK DPM_SRC_LOYALTY.BRONZE.TASK_BRONZE_TO_SILVER_POINTS_DAILY RESUME;
ALTER TASK DPM_LOYALTY_360.GOLD.TASK_SILVER_TO_GOLD_MEMBER_ENGAGEMENT RESUME;
ALTER TASK DPM_LOYALTY_360.GOLD.TASK_GENERATE_LOYALTY_DATA RESUME;
```

A child task (`..._MEMBERS_OPEN`, which runs `AFTER` the close task) has to be
resumed before its parent, or Snowflake refuses the parent's resume. Hence the
order above.

Then refresh the mirror:

```bash
cd ../backend
PYTHONPATH=. uv run python ../../ioi/dump_ddl.py ../../ioi
```

## Validating the deploy

```sql
-- bronze is one row per entity, not an event log (MERGE-on-PK)
SELECT COUNT(*) AS ROWS, COUNT(DISTINCT MEMBER_ID) AS ENTITIES
FROM DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW;   -- these two should be equal

-- the daily series really is per (member, day)
SELECT MEMBER_ID, SNAPSHOT_DATE, COUNT(*)
FROM DPM_SRC_LOYALTY.SILVER.POINTS_DAILY
GROUP BY 1, 2 HAVING COUNT(*) > 1;         -- expect zero rows

-- the dimension has exactly one current row per member
SELECT MEMBER_ID, COUNT_IF(IS_CURRENT) AS N_CURRENT
FROM DPM_SRC_LOYALTY.SILVER.MEMBERS
GROUP BY 1 HAVING COUNT_IF(IS_CURRENT) <> 1;   -- expect zero rows

-- gold reconciles against the silver series
SELECT g.MEMBER_ID, g.TOTAL_POINTS_EARNED, SUM(p.POINTS_EARNED) AS SILVER_TOTAL
FROM DPM_LOYALTY_360.GOLD.MEMBER_ENGAGEMENT g
JOIN DPM_SRC_LOYALTY.SILVER.POINTS_DAILY p ON p.MEMBER_ID = g.MEMBER_ID
GROUP BY 1, 2 HAVING g.TOTAL_POINTS_EARNED <> SUM(p.POINTS_EARNED);  -- expect zero rows
```

The third and fourth are the hand-written versions of what
`SCD2_INTEGRITY` and the reconciliation check assert. Running them by hand once
after the deploy is worth it: it separates "the check is wrong" from "the
pipeline is wrong" before anyone has to debug a red dashboard.

## What the platform derives from this

Ingesting this directory currently produces 33 proposals, including:

- layer parity at both hops for every source, with composite keys where the
  grain is composite and the MERGE's own filter lifted onto the source side
- parity against the SCD2 dimension restricted to `IS_CURRENT = TRUE`, which is
  what makes "exactly once" true for a table that holds history
- `SCD2_INTEGRITY` on `SILVER.MEMBERS`, keyed on the natural key taken from the
  MERGE rather than the surrogate key
- freshness from each writing task's own `SCHEDULE`
- the column contract of every table
- a zero-tolerance null check per `NOT NULL` column

What it does **not** yet derive, and which the loyalty pipeline is here to drive
out next: the data-quality section (null/blank/uniqueness/domain/range from a
live profile — `TIER`, `STATUS` and `CHANNEL` are closed domains and
`POINTS_EARNED` is non-negative by contract), aggregate reconciliation for
`MEMBER_ENGAGEMENT`, referential integrity from gold back to the dimension, and
the cursor-state reconciliation that `BRONZE.CURSOR_STATE` exists for.
