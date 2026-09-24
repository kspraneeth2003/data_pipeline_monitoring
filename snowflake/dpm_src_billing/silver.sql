-- DPM_SRC_BILLING.SILVER - cleaned/typed billing invoices, fed by bronze via
-- TASK_BRONZE_TO_SILVER_INVOICES (see bronze.sql). Feeds the gold 360 view
-- (see ../dpm_customer_360/gold.sql).

CREATE SCHEMA IF NOT EXISTS DPM_SRC_BILLING.SILVER;

CREATE TABLE IF NOT EXISTS DPM_SRC_BILLING.SILVER.INVOICES (
  INVOICE_ID NUMBER,
  CUSTOMER_ID NUMBER,
  -- What was bought. This is the foreign key into the product dimension in
  -- gold, and it is what makes referential integrity checkable: a
  -- transaction pointing at a product that does not exist is a real fault
  -- that no parity check between two tables can see.
  PRODUCT_ID NUMBER,
  AMOUNT NUMBER(12,2),
  STATUS STRING,
  INVOICE_DATE DATE,
  UPDATED_AT TIMESTAMP_NTZ
) COMMENT = 'Silver: cleaned/typed billing invoices';

CREATE STREAM IF NOT EXISTS DPM_SRC_BILLING.SILVER.INVOICES_STREAM
  ON TABLE DPM_SRC_BILLING.SILVER.INVOICES
  COMMENT = 'Captures changes to silver billing invoices for gold refresh';

-- PRODUCT_ID was added after this table was first deployed, and
-- `CREATE TABLE IF NOT EXISTS` above does nothing to a table that already
-- exists - so the column would silently never appear on the live table and
-- every transaction would land with a null product. Stated separately, and
-- idempotently, so a fresh deploy and an existing one converge on the same
-- shape.
ALTER TABLE DPM_SRC_BILLING.SILVER.INVOICES ADD COLUMN IF NOT EXISTS PRODUCT_ID NUMBER;
