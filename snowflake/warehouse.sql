-- DPM_PIPELINE_WH - the one warehouse every pipeline task runs on.
--
-- This was created by hand and never recorded, so its size and auto-suspend
-- were whatever the account defaulted to. That matters more than anything in
-- the task DDL: a warehouse bills per second while running, with a minimum of
-- one minute per resume, and stays running for AUTO_SUSPEND seconds after its
-- last query. At the 600s default, any task firing more often than every ten
-- minutes keeps the warehouse up around the clock.
--
-- The ALTER is there because CREATE ... IF NOT EXISTS leaves an existing
-- warehouse untouched, and the existing one is exactly the one that needs
-- fixing.

CREATE WAREHOUSE IF NOT EXISTS DPM_PIPELINE_WH
  WAREHOUSE_SIZE = XSMALL
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE
  COMMENT = 'Runs the DPM test pipeline tasks';

ALTER WAREHOUSE DPM_PIPELINE_WH SET
  WAREHOUSE_SIZE = XSMALL
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE;

-- A hard ceiling, so a task that starts misbehaving costs a capped amount
-- rather than the account. 30 credits a month is several times what the
-- guarded pipeline should use; hitting it means something is wrong.
-- Needs ACCOUNTADMIN.
CREATE RESOURCE MONITOR IF NOT EXISTS DPM_PIPELINE_MONITOR
  WITH CREDIT_QUOTA = 30
  FREQUENCY = MONTHLY
  START_TIMESTAMP = IMMEDIATELY
  TRIGGERS
    ON 75 PERCENT DO NOTIFY
    ON 100 PERCENT DO SUSPEND;

ALTER WAREHOUSE DPM_PIPELINE_WH SET RESOURCE_MONITOR = DPM_PIPELINE_MONITOR;
