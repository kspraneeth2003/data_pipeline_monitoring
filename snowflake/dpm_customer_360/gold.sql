-- DPM_CUSTOMER_360.GOLD - the unified customer model.
--
-- One individual, its satellite attributes, one dimension and two facts. The
-- layout follows the conventional 360 shape rather than a single wide table,
-- and the reason is that each table here is checkable in a different way:
--
--   INDIVIDUAL      row-for-row against the identity cross-reference, so key
--                   parity is meaningful and a missing key is a real finding
--   EMAIL           a satellite, many rows per individual - parity keyed on
--                   the individual alone would read every extra address as a
--                   duplicate, so its grain is (individual, email)
--   PRODUCT         a straight projection of inventory silver, which is the
--                   simplest possible silver -> gold parity case
--   TRANSACTION     a fact with two foreign keys, and therefore the only place
--                   referential integrity can be asserted at all
--   LOYALTY_DAILY   a fact at a composite grain, (individual, date)
--
-- A single wide CUSTOMER_360 table - which is what this database held before -
-- can only be checked by row count, because every interesting property of the
-- model has been flattened out of it.

CREATE DATABASE IF NOT EXISTS DPM_CUSTOMER_360
  COMMENT = 'The customer data product: CRM, billing, inventory and loyalty resolved into one individual';

CREATE SCHEMA IF NOT EXISTS DPM_CUSTOMER_360.GOLD;

-- ---------------------------------------------------------------------------
-- The individual, and its satellites
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS DPM_CUSTOMER_360.GOLD.INDIVIDUAL (
  INDIVIDUAL_ID STRING NOT NULL,
  FULL_NAME STRING,
  PRIMARY_EMAIL STRING,
  -- Which systems contributed to this person. Worth keeping denormalised: the
  -- first question asked of a wrong 360 row is always "where did this come
  -- from", and answering it by re-deriving the cross-reference is slow.
  SOURCE_SYSTEMS STRING,
  CRM_CUSTOMER_ID NUMBER,
  LOYALTY_MEMBER_ID NUMBER,
  -- Carried from the current version of the loyalty dimension. Closed domains:
  -- BRONZE/SILVER/GOLD/PLATINUM and ACTIVE/LAPSED/SUSPENDED.
  LOYALTY_TIER STRING,
  LOYALTY_STATUS STRING,
  SIGNUP_DATE DATE,
  ENROLLED_DATE DATE,
  UPDATED_AT TIMESTAMP_NTZ
) COMMENT = 'Gold: one row per resolved individual';

CREATE STREAM IF NOT EXISTS DPM_CUSTOMER_360.GOLD.INDIVIDUAL_STREAM
  ON TABLE DPM_CUSTOMER_360.GOLD.INDIVIDUAL
  COMMENT = 'Changes to the individual, for the BI refresh';

CREATE TABLE IF NOT EXISTS DPM_CUSTOMER_360.GOLD.EMAIL (
  INDIVIDUAL_ID STRING NOT NULL,
  EMAIL STRING NOT NULL,
  SOURCE_SYSTEM STRING NOT NULL,
  IS_PRIMARY BOOLEAN,
  UPDATED_AT TIMESTAMP_NTZ
) COMMENT = 'Gold: every known address per individual, grain (INDIVIDUAL_ID, EMAIL)';

-- ---------------------------------------------------------------------------
-- The product dimension
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS DPM_CUSTOMER_360.GOLD.PRODUCT (
  PRODUCT_ID NUMBER NOT NULL,
  PRODUCT_NAME STRING,
  CATEGORY STRING,
  UNIT_PRICE NUMBER(10,2),
  WAREHOUSE_ID STRING,
  UPDATED_AT TIMESTAMP_NTZ
) COMMENT = 'Gold: the product dimension, projected from inventory silver';

-- ---------------------------------------------------------------------------
-- The facts
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS DPM_CUSTOMER_360.GOLD.TRANSACTION (
  TRANSACTION_ID NUMBER NOT NULL,
  -- Two foreign keys, and the reason this table exists in this shape. Both
  -- must resolve: a transaction against an unknown individual is revenue
  -- attributed to nobody, and one against an unknown product is revenue
  -- attributed to nothing. Neither is visible to a parity check, because the
  -- row itself arrived perfectly intact.
  INDIVIDUAL_ID STRING,
  PRODUCT_ID NUMBER,
  AMOUNT NUMBER(12,2),
  STATUS STRING,
  TRANSACTION_DATE DATE,
  UPDATED_AT TIMESTAMP_NTZ
) COMMENT = 'Gold: billing invoices as transactions, keyed to the individual and the product';

CREATE STREAM IF NOT EXISTS DPM_CUSTOMER_360.GOLD.TRANSACTION_STREAM
  ON TABLE DPM_CUSTOMER_360.GOLD.TRANSACTION
  COMMENT = 'Changes to transactions, for the BI refresh';

CREATE TABLE IF NOT EXISTS DPM_CUSTOMER_360.GOLD.LOYALTY_DAILY (
  INDIVIDUAL_ID STRING NOT NULL,
  SNAPSHOT_DATE DATE NOT NULL,
  POINTS_EARNED NUMBER,
  POINTS_REDEEMED NUMBER,
  CHANNEL STRING,
  UPDATED_AT TIMESTAMP_NTZ
) COMMENT = 'Gold: the daily loyalty series, re-keyed onto the individual';

CREATE STREAM IF NOT EXISTS DPM_CUSTOMER_360.GOLD.LOYALTY_DAILY_STREAM
  ON TABLE DPM_CUSTOMER_360.GOLD.LOYALTY_DAILY
  COMMENT = 'Changes to the loyalty series, for the BI refresh';

-- ---------------------------------------------------------------------------
-- Silver -> Gold
-- ---------------------------------------------------------------------------

CREATE OR REPLACE TASK DPM_CUSTOMER_360.GOLD.TASK_SILVER_TO_GOLD_INDIVIDUAL
  WAREHOUSE = DPM_PIPELINE_WH
  SCHEDULE = '1 MINUTE'
AS
MERGE INTO DPM_CUSTOMER_360.GOLD.INDIVIDUAL tgt
USING (
  SELECT
      x.INDIVIDUAL_ID AS INDIVIDUAL_ID,
      COALESCE(MAX(c.FULL_NAME), MAX(m.FULL_NAME)) AS FULL_NAME,
      COALESCE(MAX(c.EMAIL), MAX(m.EMAIL)) AS PRIMARY_EMAIL,
      LISTAGG(DISTINCT x.SOURCE_SYSTEM, ',') AS SOURCE_SYSTEMS,
      MAX(c.CUSTOMER_ID) AS CRM_CUSTOMER_ID,
      MAX(m.MEMBER_ID) AS LOYALTY_MEMBER_ID,
      MAX(m.TIER) AS LOYALTY_TIER,
      MAX(m.STATUS) AS LOYALTY_STATUS,
      MAX(c.SIGNUP_DATE) AS SIGNUP_DATE,
      MAX(m.ENROLLED_DATE) AS ENROLLED_DATE
  FROM DPM_CUSTOMER_360.IDENTITY.INDIVIDUAL_XREF x
  LEFT JOIN DPM_SRC_CRM.SILVER.CUSTOMERS c
    ON x.SOURCE_SYSTEM = 'CRM' AND TO_VARCHAR(c.CUSTOMER_ID) = x.SOURCE_ID
  LEFT JOIN DPM_SRC_LOYALTY.SILVER.MEMBERS m
    ON x.SOURCE_SYSTEM = 'LOYALTY' AND TO_VARCHAR(m.MEMBER_ID) = x.SOURCE_ID
   -- Current versions only, or the dimension's history fans the join out and
   -- one member with three versions contributes three times.
   AND m.IS_CURRENT = TRUE
  GROUP BY x.INDIVIDUAL_ID
) src
ON tgt.INDIVIDUAL_ID = src.INDIVIDUAL_ID
WHEN MATCHED THEN UPDATE SET
  FULL_NAME = src.FULL_NAME, PRIMARY_EMAIL = src.PRIMARY_EMAIL,
  SOURCE_SYSTEMS = src.SOURCE_SYSTEMS, CRM_CUSTOMER_ID = src.CRM_CUSTOMER_ID,
  LOYALTY_MEMBER_ID = src.LOYALTY_MEMBER_ID, LOYALTY_TIER = src.LOYALTY_TIER,
  LOYALTY_STATUS = src.LOYALTY_STATUS, SIGNUP_DATE = src.SIGNUP_DATE,
  ENROLLED_DATE = src.ENROLLED_DATE, UPDATED_AT = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (
  INDIVIDUAL_ID, FULL_NAME, PRIMARY_EMAIL, SOURCE_SYSTEMS, CRM_CUSTOMER_ID,
  LOYALTY_MEMBER_ID, LOYALTY_TIER, LOYALTY_STATUS, SIGNUP_DATE, ENROLLED_DATE, UPDATED_AT
) VALUES (
  src.INDIVIDUAL_ID, src.FULL_NAME, src.PRIMARY_EMAIL, src.SOURCE_SYSTEMS, src.CRM_CUSTOMER_ID,
  src.LOYALTY_MEMBER_ID, src.LOYALTY_TIER, src.LOYALTY_STATUS, src.SIGNUP_DATE, src.ENROLLED_DATE,
  CURRENT_TIMESTAMP()
);

CREATE OR REPLACE TASK DPM_CUSTOMER_360.GOLD.TASK_SILVER_TO_GOLD_EMAIL
  WAREHOUSE = DPM_PIPELINE_WH
  AFTER DPM_CUSTOMER_360.GOLD.TASK_SILVER_TO_GOLD_INDIVIDUAL
AS
MERGE INTO DPM_CUSTOMER_360.GOLD.EMAIL tgt
USING (
  SELECT
      x.INDIVIDUAL_ID AS INDIVIDUAL_ID,
      n.NORMALIZED_EMAIL AS EMAIL,
      n.SOURCE_SYSTEM AS SOURCE_SYSTEM,
      n.NORMALIZED_EMAIL = MAX(n.NORMALIZED_EMAIL) OVER (PARTITION BY x.INDIVIDUAL_ID) AS IS_PRIMARY
  FROM DPM_CUSTOMER_360.IDENTITY.NORMALIZE_EMAIL n
  JOIN DPM_CUSTOMER_360.IDENTITY.INDIVIDUAL_XREF x
    ON x.SOURCE_SYSTEM = n.SOURCE_SYSTEM AND x.SOURCE_ID = n.SOURCE_ID
  WHERE n.NORMALIZED_EMAIL IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY x.INDIVIDUAL_ID, n.NORMALIZED_EMAIL
    ORDER BY n.SOURCE_SYSTEM
  ) = 1
) src
ON tgt.INDIVIDUAL_ID = src.INDIVIDUAL_ID AND tgt.EMAIL = src.EMAIL
WHEN MATCHED THEN UPDATE SET
  SOURCE_SYSTEM = src.SOURCE_SYSTEM, IS_PRIMARY = src.IS_PRIMARY, UPDATED_AT = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (INDIVIDUAL_ID, EMAIL, SOURCE_SYSTEM, IS_PRIMARY, UPDATED_AT)
  VALUES (src.INDIVIDUAL_ID, src.EMAIL, src.SOURCE_SYSTEM, src.IS_PRIMARY, CURRENT_TIMESTAMP());

CREATE OR REPLACE TASK DPM_CUSTOMER_360.GOLD.TASK_SILVER_TO_GOLD_PRODUCT
  WAREHOUSE = DPM_PIPELINE_WH
  SCHEDULE = '1 MINUTE'
  WHEN SYSTEM$STREAM_HAS_DATA('DPM_SRC_INVENTORY.SILVER.PRODUCTS_STREAM')
AS
MERGE INTO DPM_CUSTOMER_360.GOLD.PRODUCT tgt
USING (
  SELECT
      p.PRODUCT_ID AS PRODUCT_ID,
      p.PRODUCT_NAME AS PRODUCT_NAME,
      p.CATEGORY AS CATEGORY,
      p.UNIT_PRICE AS UNIT_PRICE,
      p.WAREHOUSE_ID AS WAREHOUSE_ID
  FROM DPM_SRC_INVENTORY.SILVER.PRODUCTS p
  WHERE p.PRODUCT_ID IN (SELECT PRODUCT_ID FROM DPM_SRC_INVENTORY.SILVER.PRODUCTS_STREAM)
) src
ON tgt.PRODUCT_ID = src.PRODUCT_ID
WHEN MATCHED THEN UPDATE SET
  PRODUCT_NAME = src.PRODUCT_NAME, CATEGORY = src.CATEGORY, UNIT_PRICE = src.UNIT_PRICE,
  WAREHOUSE_ID = src.WAREHOUSE_ID, UPDATED_AT = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (PRODUCT_ID, PRODUCT_NAME, CATEGORY, UNIT_PRICE, WAREHOUSE_ID, UPDATED_AT)
  VALUES (src.PRODUCT_ID, src.PRODUCT_NAME, src.CATEGORY, src.UNIT_PRICE, src.WAREHOUSE_ID, CURRENT_TIMESTAMP());

CREATE OR REPLACE TASK DPM_CUSTOMER_360.GOLD.TASK_SILVER_TO_GOLD_TRANSACTION
  WAREHOUSE = DPM_PIPELINE_WH
  SCHEDULE = '1 MINUTE'
  WHEN SYSTEM$STREAM_HAS_DATA('DPM_SRC_BILLING.SILVER.INVOICES_STREAM')
AS
MERGE INTO DPM_CUSTOMER_360.GOLD.TRANSACTION tgt
USING (
  SELECT
      i.INVOICE_ID AS TRANSACTION_ID,
      x.INDIVIDUAL_ID AS INDIVIDUAL_ID,
      i.PRODUCT_ID AS PRODUCT_ID,
      i.AMOUNT AS AMOUNT,
      i.STATUS AS STATUS,
      i.INVOICE_DATE AS TRANSACTION_DATE
  FROM DPM_SRC_BILLING.SILVER.INVOICES i
  -- A LEFT join on purpose. An invoice whose customer did not resolve still
  -- belongs in the 360 - dropping it would hide lost revenue by making the
  -- table smaller, which is the quietest possible failure. It lands with a
  -- null INDIVIDUAL_ID instead, where the referential-integrity check can see
  -- it and say so.
  LEFT JOIN DPM_CUSTOMER_360.IDENTITY.INDIVIDUAL_XREF x
    ON x.SOURCE_SYSTEM = 'CRM' AND x.SOURCE_ID = TO_VARCHAR(i.CUSTOMER_ID)
  WHERE i.INVOICE_ID IN (SELECT INVOICE_ID FROM DPM_SRC_BILLING.SILVER.INVOICES_STREAM)
) src
ON tgt.TRANSACTION_ID = src.TRANSACTION_ID
WHEN MATCHED THEN UPDATE SET
  INDIVIDUAL_ID = src.INDIVIDUAL_ID, PRODUCT_ID = src.PRODUCT_ID, AMOUNT = src.AMOUNT,
  STATUS = src.STATUS, TRANSACTION_DATE = src.TRANSACTION_DATE, UPDATED_AT = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (
  TRANSACTION_ID, INDIVIDUAL_ID, PRODUCT_ID, AMOUNT, STATUS, TRANSACTION_DATE, UPDATED_AT
) VALUES (
  src.TRANSACTION_ID, src.INDIVIDUAL_ID, src.PRODUCT_ID, src.AMOUNT, src.STATUS,
  src.TRANSACTION_DATE, CURRENT_TIMESTAMP()
);

CREATE OR REPLACE TASK DPM_CUSTOMER_360.GOLD.TASK_SILVER_TO_GOLD_LOYALTY_DAILY
  WAREHOUSE = DPM_PIPELINE_WH
  SCHEDULE = '1 MINUTE'
  WHEN SYSTEM$STREAM_HAS_DATA('DPM_SRC_LOYALTY.SILVER.POINTS_DAILY_STREAM')
AS
MERGE INTO DPM_CUSTOMER_360.GOLD.LOYALTY_DAILY tgt
USING (
  SELECT
      x.INDIVIDUAL_ID AS INDIVIDUAL_ID,
      p.SNAPSHOT_DATE AS SNAPSHOT_DATE,
      p.POINTS_EARNED AS POINTS_EARNED,
      p.POINTS_REDEEMED AS POINTS_REDEEMED,
      p.CHANNEL AS CHANNEL
  FROM DPM_SRC_LOYALTY.SILVER.POINTS_DAILY p
  JOIN DPM_CUSTOMER_360.IDENTITY.INDIVIDUAL_XREF x
    ON x.SOURCE_SYSTEM = 'LOYALTY' AND x.SOURCE_ID = TO_VARCHAR(p.MEMBER_ID)
  WHERE p.MEMBER_ID IN (SELECT MEMBER_ID FROM DPM_SRC_LOYALTY.SILVER.POINTS_DAILY_STREAM)
  -- Two loyalty members resolving to one individual would otherwise land two
  -- rows for the same (individual, day) and break the declared grain.
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY x.INDIVIDUAL_ID, p.SNAPSHOT_DATE
    ORDER BY p.MEMBER_ID
  ) = 1
) src
ON tgt.INDIVIDUAL_ID = src.INDIVIDUAL_ID AND tgt.SNAPSHOT_DATE = src.SNAPSHOT_DATE
WHEN MATCHED THEN UPDATE SET
  POINTS_EARNED = src.POINTS_EARNED, POINTS_REDEEMED = src.POINTS_REDEEMED,
  CHANNEL = src.CHANNEL, UPDATED_AT = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (
  INDIVIDUAL_ID, SNAPSHOT_DATE, POINTS_EARNED, POINTS_REDEEMED, CHANNEL, UPDATED_AT
) VALUES (
  src.INDIVIDUAL_ID, src.SNAPSHOT_DATE, src.POINTS_EARNED, src.POINTS_REDEEMED,
  src.CHANNEL, CURRENT_TIMESTAMP()
);
