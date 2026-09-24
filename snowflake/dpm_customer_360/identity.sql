-- DPM_CUSTOMER_360.IDENTITY - resolving one person across several sources.
--
-- This schema is what makes the database a 360 rather than a join. The CRM
-- knows a customer by CUSTOMER_ID; loyalty knows the same human by MEMBER_ID
-- and has never heard of the CRM. Nothing downstream can count a person once
-- until something decides those two records are the same person.
--
-- The resolution here is deliberately simple - normalise the email, and treat
-- records sharing a normalised email as one individual. Real identity
-- resolution adds phone, name and address matching and a connected-components
-- pass over the resulting graph. That is a different problem; what matters for
-- this pipeline is that the *shape* is right, because the shape is what
-- generates the interesting checks:
--
--   * INDIVIDUAL_XREF must be a function, not a relation. One (source system,
--     source id) maps to exactly one individual. More than one means the
--     resolver ran twice and disagreed with itself, and every count downstream
--     is then inflated by an amount nobody can see.
--   * Every source record must resolve. A customer with no row here vanishes
--     from the 360 silently - the gold table is simply smaller, which looks
--     like a quiet pipeline rather than a fault.
--   * Normalisation must be total. A row in NORMALIZE_EMAIL with a null
--     normalised value is a record that can never match anything.
--
-- None of those are visible to a parity check between two tables, which is the
-- point of putting them here.

CREATE DATABASE IF NOT EXISTS DPM_CUSTOMER_360
  COMMENT = 'The customer data product: CRM, billing, inventory and loyalty resolved into one individual';

CREATE SCHEMA IF NOT EXISTS DPM_CUSTOMER_360.IDENTITY;

-- ---------------------------------------------------------------------------
-- Normalisation
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS DPM_CUSTOMER_360.IDENTITY.NORMALIZE_EMAIL (
  SOURCE_SYSTEM STRING NOT NULL,
  SOURCE_ID STRING NOT NULL,
  RAW_EMAIL STRING,
  -- Lower-cased, trimmed, and with any `+tag` suffix removed, because
  -- `ana+shop@x.com` and `ana@x.com` are one mailbox and one person. Null when
  -- the source had no usable email - kept as a row rather than dropped, so
  -- "how many records cannot be matched at all" stays answerable.
  NORMALIZED_EMAIL STRING,
  UPDATED_AT TIMESTAMP_NTZ
) COMMENT = 'Identity: one row per source record, with its email normalised for matching';

-- ---------------------------------------------------------------------------
-- The cross-reference
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS DPM_CUSTOMER_360.IDENTITY.INDIVIDUAL_XREF (
  SOURCE_SYSTEM STRING NOT NULL,
  SOURCE_ID STRING NOT NULL,
  -- Derived from the normalised email rather than allocated from a sequence,
  -- so the resolver is idempotent: re-running it on unchanged input produces
  -- the same ids. A sequence would mint new ids on every run and silently
  -- re-key the whole 360.
  INDIVIDUAL_ID STRING NOT NULL,
  MATCH_RULE STRING NOT NULL,
  RESOLVED_AT TIMESTAMP_NTZ
) COMMENT = 'Identity: (source system, source id) -> individual. Must be a function, not a relation';

-- ---------------------------------------------------------------------------
-- The resolver
-- ---------------------------------------------------------------------------
--
-- Full rebuild rather than incremental. Identity is global: one new record can
-- merge two individuals who were previously distinct, so there is no such
-- thing as resolving a row in isolation. At this scale a rebuild is cheap and
-- correct; an incremental resolver that cannot merge is neither.

CREATE OR REPLACE PROCEDURE DPM_CUSTOMER_360.IDENTITY.SP_RESOLVE_IDENTITY()
RETURNS VARCHAR
LANGUAGE SQL
COMMENT = 'Rebuilds NORMALIZE_EMAIL and INDIVIDUAL_XREF from the CRM and loyalty silver layers'
EXECUTE AS OWNER
AS
$$
DECLARE
  normalized INTEGER DEFAULT 0;
  resolved INTEGER DEFAULT 0;
  individuals INTEGER DEFAULT 0;
BEGIN
  -- Rebuilt in place. TRUNCATE + INSERT rather than CREATE OR REPLACE keeps
  -- the table's identity, its grants and any stream on it intact.
  TRUNCATE TABLE DPM_CUSTOMER_360.IDENTITY.NORMALIZE_EMAIL;

  INSERT INTO DPM_CUSTOMER_360.IDENTITY.NORMALIZE_EMAIL
    (SOURCE_SYSTEM, SOURCE_ID, RAW_EMAIL, NORMALIZED_EMAIL, UPDATED_AT)
  SELECT
      'CRM',
      TO_VARCHAR(c.CUSTOMER_ID),
      c.EMAIL,
      NULLIF(REGEXP_REPLACE(LOWER(TRIM(c.EMAIL)), '[+][^@]*@', '@'), ''),
      CURRENT_TIMESTAMP()
  FROM DPM_SRC_CRM.SILVER.CUSTOMERS c
  UNION ALL
  SELECT
      'LOYALTY',
      TO_VARCHAR(m.MEMBER_ID),
      m.EMAIL,
      NULLIF(REGEXP_REPLACE(LOWER(TRIM(m.EMAIL)), '[+][^@]*@', '@'), ''),
      CURRENT_TIMESTAMP()
  FROM DPM_SRC_LOYALTY.SILVER.MEMBERS m
  -- Current versions only. The dimension keeps history; identity is about who
  -- the person is now, and matching on superseded emails would resurrect
  -- addresses the person has already replaced.
  WHERE m.IS_CURRENT = TRUE;

  normalized := SQLROWCOUNT;

  TRUNCATE TABLE DPM_CUSTOMER_360.IDENTITY.INDIVIDUAL_XREF;

  INSERT INTO DPM_CUSTOMER_360.IDENTITY.INDIVIDUAL_XREF
    (SOURCE_SYSTEM, SOURCE_ID, INDIVIDUAL_ID, MATCH_RULE, RESOLVED_AT)
  SELECT
      n.SOURCE_SYSTEM,
      n.SOURCE_ID,
      -- Records sharing a normalised email are one person. Records without one
      -- cannot match anybody, so each becomes an individual of its own keyed on
      -- its source identity - dropping them instead would make the customer
      -- disappear from the 360 with nothing to show it had ever been there.
      CASE
        WHEN n.NORMALIZED_EMAIL IS NOT NULL THEN MD5(n.NORMALIZED_EMAIL)
        ELSE MD5(n.SOURCE_SYSTEM || ':' || n.SOURCE_ID)
      END,
      CASE
        WHEN n.NORMALIZED_EMAIL IS NOT NULL THEN 'EMAIL_EXACT'
        ELSE 'UNMATCHABLE_SINGLETON'
      END,
      CURRENT_TIMESTAMP()
  FROM DPM_CUSTOMER_360.IDENTITY.NORMALIZE_EMAIL n
  -- One row per source record. A source that lands the same id twice would
  -- otherwise make the cross-reference a relation rather than a function, and
  -- every downstream count would double for that person only.
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY n.SOURCE_SYSTEM, n.SOURCE_ID
    ORDER BY n.UPDATED_AT DESC
  ) = 1;

  resolved := SQLROWCOUNT;

  SELECT COUNT(DISTINCT INDIVIDUAL_ID) INTO :individuals
  FROM DPM_CUSTOMER_360.IDENTITY.INDIVIDUAL_XREF;

  RETURN 'Normalised ' || normalized || ' source record(s); resolved ' || resolved
      || ' of them onto ' || individuals || ' individual(s).';
END;
$$;

CREATE OR REPLACE TASK DPM_CUSTOMER_360.IDENTITY.TASK_RESOLVE_IDENTITY
  WAREHOUSE = DPM_PIPELINE_WH
  SCHEDULE = '5 MINUTE'
  COMMENT = 'Rebuilds the identity cross-reference that the gold layer keys on'
AS
CALL DPM_CUSTOMER_360.IDENTITY.SP_RESOLVE_IDENTITY();
