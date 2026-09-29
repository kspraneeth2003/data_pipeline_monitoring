# The test pipeline, as reviewed intent

Idempotent, reviewed DDL, tracked in git so the RCA agent can attribute a
failing object to a commit and an author. `../../ioi/` is the other half — a
`GET_DDL` mirror of what the account actually contains. They are expected to
drift, and the drift is the interesting part.

## Shape: many sources, one data product

Four sources feeding **one** 360, which is the shape a real pipeline has — FCC
runs fourteen sources into a single Fan360. This repo previously had three gold
databases, one per source, which is not a data product but three small
pipelines wearing one.

| Database | What it exercises |
|---|---|
| `DPM_SRC_CRM` | append-only VARIANT landing → MERGE dedup into typed silver |
| `DPM_SRC_BILLING` | the same, plus a closed-domain `STATUS` and the product foreign key |
| `DPM_SRC_INVENTORY` | the same, plus a **filtered** MERGE and the standing mis-mapped-payload bug |
| `DPM_SRC_LOYALTY` | **CDC / MERGE-on-PK bronze**, composite snapshot grain, **SCD2 dimension**, loader cursor state |
| `DPM_CUSTOMER_360` | identity resolution, the unified model, and the reporting layer |

`DPM_CUSTOMER_360` has three schemas, following the same split FCC uses:

- **`IDENTITY`** — the part that makes this a 360 rather than a join. The CRM
  knows a customer by `CUSTOMER_ID`; loyalty knows the same human by
  `MEMBER_ID` and has never heard of the CRM. `INDIVIDUAL_XREF` decides they
  are one person. Its own correctness is checkable and nothing downstream can
  check it: the cross-reference must be a *function*, every source record must
  resolve, and normalisation must be total.
- **`GOLD`** — `INDIVIDUAL` and its `EMAIL` satellite, a `PRODUCT` dimension,
  and two facts (`TRANSACTION`, `LOYALTY_DAILY`). Deliberately not one wide
  table: a wide table can only be checked by row count, because every
  interesting property of the model has been flattened out of it. `TRANSACTION`
  carries two foreign keys and is therefore the only place referential
  integrity can be asserted at all.
- **`BI`** — derived numbers, where key parity is meaningless by construction
  (ninety `LOYALTY_DAILY` rows behind one `INDIVIDUAL_ENGAGEMENT` row) and
  reconciliation is the only true statement about the hop.

## Deploying

Everything is idempotent. Note that `CREATE OR REPLACE TASK` resets a task's
state and `CREATE OR REPLACE STREAM` **discards an unconsumed stream offset**,
which drops whatever that stream had not yet moved — fine on a test account,
not fine anywhere else.

Order: suspend running tasks first, then silver before bronze (bronze holds the
tasks that MERGE into silver), then identity before gold before BI.

```sql
@dpm_src_crm/silver.sql        @dpm_src_crm/bronze.sql
@dpm_src_billing/silver.sql    @dpm_src_billing/bronze.sql
@dpm_src_inventory/silver.sql  @dpm_src_inventory/bronze.sql
@dpm_src_loyalty/silver.sql    @dpm_src_loyalty/bronze.sql
@dpm_customer_360/identity.sql
@dpm_customer_360/gold.sql
@dpm_customer_360/bi.sql
@dpm_customer_360/generator.sql
@dpm_customer_360/backfill.sql
```

Then prime it, backfill, and resume:

```sql
CALL DPM_CUSTOMER_360.GOLD.SP_GENERATE_TEST_DATA(30, 3, 20, 5, 2, 30);
CALL DPM_CUSTOMER_360.GOLD.SP_BACKFILL_GOLD();
```

**The backfill is part of deploying, not an afterthought.** The silver → gold
tasks are stream-driven, so they only ever move what changed since they last
ran — everything already sitting in silver was never in a stream and would
never arrive. Skip it and parity correctly reports thousands of missing keys on
a pipeline that is working perfectly from that point on, which is how a check
loses its reader's trust permanently.

Resume children before roots, or Snowflake refuses the root:

```sql
ALTER TASK DPM_SRC_LOYALTY.BRONZE.TASK_BRONZE_TO_SILVER_MEMBERS_OPEN RESUME;
ALTER TASK DPM_CUSTOMER_360.GOLD.TASK_SILVER_TO_GOLD_EMAIL RESUME;
-- ...then every other task.
```

Refresh the mirror afterwards:

```bash
cd ../backend && PYTHONPATH=. uv run python ../../ioi/dump_ddl.py ../../ioi
```

## Validating a deploy

Run these by hand once. They are the hand-written versions of what the platform
asserts, and running them separates "the check is wrong" from "the pipeline is
wrong" before anyone has to debug a red dashboard.

```sql
-- identity resolved, and actually matched across sources
SELECT COUNT(*) rows, COUNT(DISTINCT INDIVIDUAL_ID) individuals
FROM DPM_CUSTOMER_360.IDENTITY.INDIVIDUAL_XREF;
SELECT COUNT(*) FROM (
  SELECT INDIVIDUAL_ID FROM DPM_CUSTOMER_360.IDENTITY.INDIVIDUAL_XREF
  GROUP BY 1 HAVING COUNT(DISTINCT SOURCE_SYSTEM) > 1);   -- must be > 0

-- the cross-reference is a function, not a relation
SELECT COUNT(*) FROM (
  SELECT SOURCE_SYSTEM, SOURCE_ID FROM DPM_CUSTOMER_360.IDENTITY.INDIVIDUAL_XREF
  GROUP BY 1,2 HAVING COUNT(DISTINCT INDIVIDUAL_ID) > 1);  -- expect 0

-- SCD2: exactly one current row per member, no malformed windows
SELECT COUNT(*) FROM (SELECT MEMBER_ID FROM DPM_SRC_LOYALTY.SILVER.MEMBERS
  GROUP BY 1 HAVING COUNT_IF(IS_CURRENT) <> 1);            -- expect 0
SELECT COUNT(*) FROM DPM_SRC_LOYALTY.SILVER.MEMBERS
 WHERE VALID_FROM >= VALID_TO;                             -- expect 0

-- referential integrity out of the fact table
SELECT COUNT_IF(INDIVIDUAL_ID IS NULL) FROM DPM_CUSTOMER_360.GOLD.TRANSACTION;
SELECT COUNT(*) FROM DPM_CUSTOMER_360.GOLD.TRANSACTION t
  LEFT JOIN DPM_CUSTOMER_360.GOLD.PRODUCT p ON p.PRODUCT_ID = t.PRODUCT_ID
 WHERE t.PRODUCT_ID IS NOT NULL AND p.PRODUCT_ID IS NULL;  -- expect 0

-- BI reconciles against the gold facts
SELECT COUNT(*) FROM DPM_CUSTOMER_360.BI.INDIVIDUAL_ENGAGEMENT e
  LEFT JOIN (SELECT INDIVIDUAL_ID, SUM(POINTS_EARNED) s
             FROM DPM_CUSTOMER_360.GOLD.LOYALTY_DAILY GROUP BY 1) g
    ON g.INDIVIDUAL_ID = e.INDIVIDUAL_ID
 WHERE e.TOTAL_POINTS_EARNED <> COALESCE(g.s, 0);          -- expect 0
```

Last deployed 2026-09-24. All nine passed, against 1,080 individuals resolved
from 1,878 source records, 798 of them present in both CRM and loyalty.

## What the platform derives from this

66 proposals, with parity on 15 of 18 tables: layer parity at both hops for
every source, composite keys where the grain is composite, the MERGE's own
filter lifted onto the source side, parity against the SCD2 dimension
restricted to `IS_CURRENT = TRUE`, `SCD2_INTEGRITY` keyed on the natural key
taken from the MERGE, freshness from each task's own `SCHEDULE`, the column
contract of every table, and a zero-tolerance null check per `NOT NULL` column.

Still not derived, and what this pipeline exists to drive out next: the
data-quality section (`TIER`, `STATUS`, `CATEGORY` and `CHANNEL` are closed
domains; `POINTS_EARNED` is non-negative by contract), aggregate reconciliation
for `BI.INDIVIDUAL_ENGAGEMENT`, referential integrity out of
`GOLD.TRANSACTION`, the identity assertions above, and the cursor-state
reconciliation `BRONZE.CURSOR_STATE` exists for.
