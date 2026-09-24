-- DPM_CUSTOMER_360 - the one-off backfill.
--
-- The silver -> gold tasks are driven by streams, so each one only ever moves
-- what changed since it last ran. That is right for steady state and wrong for
-- the first run: everything already sitting in silver when the task was created
-- was never in a stream, so gold starts out holding only the rows that have
-- changed since deployment.
--
-- Left alone that is not a small cosmetic gap. Parity between silver and gold
-- would correctly report thousands of missing keys, on a pipeline that is
-- working exactly as designed from this point forward - and a check that opens
-- with thousands of false findings is a check nobody will ever trust again.
--
-- So the backfill is part of deploying, not an afterthought. It is the same
-- projection each task performs, with the stream predicate removed and nothing
-- else changed: run it once after the first deploy, and again after any change
-- that widens what gold holds.
--
-- Idempotent - every statement is a MERGE on the target's own key, so running
-- it twice changes nothing the second time.

CREATE OR REPLACE PROCEDURE DPM_CUSTOMER_360.GOLD.SP_BACKFILL_GOLD()
RETURNS VARCHAR
LANGUAGE SQL
COMMENT = 'One-off: loads everything already in silver into gold, which the stream-driven tasks cannot see'
EXECUTE AS OWNER
AS
$$
DECLARE
  products INTEGER DEFAULT 0;
  transactions INTEGER DEFAULT 0;
  loyalty_days INTEGER DEFAULT 0;
BEGIN
  -- Identity first. Every gold table below keys on the cross-reference, so a
  -- stale one silently drops whichever source it has not yet seen.
  CALL DPM_CUSTOMER_360.IDENTITY.SP_RESOLVE_IDENTITY();

  MERGE INTO DPM_CUSTOMER_360.GOLD.PRODUCT tgt
  USING (
    SELECT PRODUCT_ID, PRODUCT_NAME, CATEGORY, UNIT_PRICE, WAREHOUSE_ID
    FROM DPM_SRC_INVENTORY.SILVER.PRODUCTS
  ) src
  ON tgt.PRODUCT_ID = src.PRODUCT_ID
  WHEN MATCHED THEN UPDATE SET
    PRODUCT_NAME = src.PRODUCT_NAME, CATEGORY = src.CATEGORY,
    UNIT_PRICE = src.UNIT_PRICE, WAREHOUSE_ID = src.WAREHOUSE_ID,
    UPDATED_AT = CURRENT_TIMESTAMP()
  WHEN NOT MATCHED THEN INSERT (PRODUCT_ID, PRODUCT_NAME, CATEGORY, UNIT_PRICE, WAREHOUSE_ID, UPDATED_AT)
    VALUES (src.PRODUCT_ID, src.PRODUCT_NAME, src.CATEGORY, src.UNIT_PRICE, src.WAREHOUSE_ID, CURRENT_TIMESTAMP());
  products := SQLROWCOUNT;

  MERGE INTO DPM_CUSTOMER_360.GOLD.TRANSACTION tgt
  USING (
    SELECT
        i.INVOICE_ID AS TRANSACTION_ID, x.INDIVIDUAL_ID AS INDIVIDUAL_ID,
        i.PRODUCT_ID AS PRODUCT_ID, i.AMOUNT AS AMOUNT, i.STATUS AS STATUS,
        i.INVOICE_DATE AS TRANSACTION_DATE
    FROM DPM_SRC_BILLING.SILVER.INVOICES i
    LEFT JOIN DPM_CUSTOMER_360.IDENTITY.INDIVIDUAL_XREF x
      ON x.SOURCE_SYSTEM = 'CRM' AND x.SOURCE_ID = TO_VARCHAR(i.CUSTOMER_ID)
  ) src
  ON tgt.TRANSACTION_ID = src.TRANSACTION_ID
  WHEN MATCHED THEN UPDATE SET
    INDIVIDUAL_ID = src.INDIVIDUAL_ID, PRODUCT_ID = src.PRODUCT_ID, AMOUNT = src.AMOUNT,
    STATUS = src.STATUS, TRANSACTION_DATE = src.TRANSACTION_DATE, UPDATED_AT = CURRENT_TIMESTAMP()
  WHEN NOT MATCHED THEN INSERT (TRANSACTION_ID, INDIVIDUAL_ID, PRODUCT_ID, AMOUNT, STATUS, TRANSACTION_DATE, UPDATED_AT)
    VALUES (src.TRANSACTION_ID, src.INDIVIDUAL_ID, src.PRODUCT_ID, src.AMOUNT, src.STATUS, src.TRANSACTION_DATE, CURRENT_TIMESTAMP());
  transactions := SQLROWCOUNT;

  MERGE INTO DPM_CUSTOMER_360.GOLD.LOYALTY_DAILY tgt
  USING (
    SELECT
        x.INDIVIDUAL_ID AS INDIVIDUAL_ID, p.SNAPSHOT_DATE AS SNAPSHOT_DATE,
        p.POINTS_EARNED AS POINTS_EARNED, p.POINTS_REDEEMED AS POINTS_REDEEMED,
        p.CHANNEL AS CHANNEL
    FROM DPM_SRC_LOYALTY.SILVER.POINTS_DAILY p
    JOIN DPM_CUSTOMER_360.IDENTITY.INDIVIDUAL_XREF x
      ON x.SOURCE_SYSTEM = 'LOYALTY' AND x.SOURCE_ID = TO_VARCHAR(p.MEMBER_ID)
    QUALIFY ROW_NUMBER() OVER (
      PARTITION BY x.INDIVIDUAL_ID, p.SNAPSHOT_DATE ORDER BY p.MEMBER_ID
    ) = 1
  ) src
  ON tgt.INDIVIDUAL_ID = src.INDIVIDUAL_ID AND tgt.SNAPSHOT_DATE = src.SNAPSHOT_DATE
  WHEN MATCHED THEN UPDATE SET
    POINTS_EARNED = src.POINTS_EARNED, POINTS_REDEEMED = src.POINTS_REDEEMED,
    CHANNEL = src.CHANNEL, UPDATED_AT = CURRENT_TIMESTAMP()
  WHEN NOT MATCHED THEN INSERT (INDIVIDUAL_ID, SNAPSHOT_DATE, POINTS_EARNED, POINTS_REDEEMED, CHANNEL, UPDATED_AT)
    VALUES (src.INDIVIDUAL_ID, src.SNAPSHOT_DATE, src.POINTS_EARNED, src.POINTS_REDEEMED, src.CHANNEL, CURRENT_TIMESTAMP());
  loyalty_days := SQLROWCOUNT;

  RETURN 'Backfilled ' || products || ' product(s), ' || transactions
      || ' transaction(s) and ' || loyalty_days || ' loyalty day(s).';
END;
$$;
