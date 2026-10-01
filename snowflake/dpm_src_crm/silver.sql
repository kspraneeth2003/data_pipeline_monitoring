-- DPM_SRC_CRM.SILVER - cleaned/typed CRM customers, fed by bronze via
-- TASK_BRONZE_TO_SILVER_CUSTOMERS (see bronze.sql). Feeds the gold 360 view
-- (see ../dpm_customer_360/gold.sql).

CREATE SCHEMA IF NOT EXISTS DPM_SRC_CRM.SILVER;

CREATE TABLE IF NOT EXISTS DPM_SRC_CRM.SILVER.CUSTOMERS (
  CUSTOMER_ID NUMBER,
  FULL_NAME STRING,
  EMAIL STRING,
  SIGNUP_DATE DATE,
  UPDATED_AT TIMESTAMP_NTZ
) COMMENT = 'Silver: cleaned/typed CRM customers';

CREATE STREAM IF NOT EXISTS DPM_SRC_CRM.SILVER.CUSTOMERS_STREAM
  ON TABLE DPM_SRC_CRM.SILVER.CUSTOMERS
  COMMENT = 'Captures changes to silver CRM customers for gold refresh';

-- The identity resolver's own trigger. A stream has one offset, and whichever
-- task consumes it first empties it for everyone else - two tasks sharing
-- CUSTOMERS_STREAM would each miss the changes the other one ran on. So every
-- consumer gets its own stream on the table.
CREATE STREAM IF NOT EXISTS DPM_SRC_CRM.SILVER.CUSTOMERS_IDENTITY_STREAM
  ON TABLE DPM_SRC_CRM.SILVER.CUSTOMERS
  COMMENT = 'Changes to silver CRM customers, consumed only by the identity resolver';
