-- DPM_CUSTOMER_360.GOLD - the test-data generator for the whole pipeline.
--
-- One generator rather than one per source, because the interesting property
-- of this data product is that the *same person* appears in more than one
-- source. Generating CRM customers and loyalty members independently would
-- produce two disjoint populations that never resolve to a shared individual,
-- and the identity layer - the thing that makes this a 360 - would be
-- exercised only in the trivial case where every individual has exactly one
-- source record.
--
-- So the loyalty members are drawn deliberately from existing CRM customers,
-- reusing their email, and a share of CRM customers are left with no loyalty
-- record at all. That gives all three populations worth checking: people in
-- both systems, people in one, and people in the other.
--
-- It stands in for four loaders and is not part of the pipeline under test.

CREATE OR REPLACE PROCEDURE DPM_CUSTOMER_360.GOLD.SP_GENERATE_TEST_DATA(
  "NUM_CUSTOMERS" NUMBER,
  "NUM_INVOICES_PER_CUSTOMER" NUMBER,
  "NUM_PRODUCTS" NUMBER,
  "NUM_LOYALTY_UPDATES" NUMBER,
  "NUM_LOYALTY_DELETES" NUMBER,
  "NUM_SNAPSHOT_DAYS" NUMBER
)
RETURNS VARCHAR
LANGUAGE SQL
COMMENT = 'Generates CRM customers, billing invoices, inventory products and loyalty CDC events, with deliberate overlap between CRM and loyalty so identity resolution is exercised'
EXECUTE AS OWNER
AS
$$
DECLARE
  i INTEGER DEFAULT 0;
  j INTEGER DEFAULT 0;
  new_cust_id INTEGER;
  new_product_id INTEGER;
  next_seq INTEGER;
  product_count INTEGER DEFAULT 0;
  invoices_written INTEGER DEFAULT 0;
  members_written INTEGER DEFAULT 0;
  snapshot_rows INTEGER DEFAULT 0;
  existing_members INTEGER DEFAULT 0;
BEGIN
  -- ---- products first ------------------------------------------------------
  -- Invoices reference a product, so the dimension has to exist before the
  -- facts that point at it. Generating them the other way round would make
  -- every new invoice fail referential integrity for reasons that are the
  -- generator's fault rather than the pipeline's.
  WHILE (i < NUM_PRODUCTS) DO
    SELECT DPM_SRC_INVENTORY.BRONZE.SEQ_PRODUCT_ID.NEXTVAL INTO :new_product_id;

    INSERT INTO DPM_SRC_INVENTORY.BRONZE.PRODUCTS_RAW (RAW_PAYLOAD)
    SELECT OBJECT_CONSTRUCT(
      'product_id', :new_product_id,
      'product_name', 'Product ' || :new_product_id,
      'category', ARRAY_CONSTRUCT('ELECTRONICS','GROCERY','APPAREL','HOME')[UNIFORM(0, 3, RANDOM())],
      'unit_price', ROUND(UNIFORM(5, 500, RANDOM()) + UNIFORM(0, 99, RANDOM()) / 100.0, 2),
      -- Roughly a third land out of stock, which is what the filtered MERGE
      -- into silver exists to exclude.
      'quantity_on_hand', CASE WHEN UNIFORM(0, 9, RANDOM()) < 3 THEN 0 ELSE UNIFORM(1, 200, RANDOM()) END,
      'warehouse_id', 'WH-' || UNIFORM(1, 5, RANDOM())
    );
    i := i + 1;
  END WHILE;

  SELECT COUNT(*) INTO :product_count FROM DPM_SRC_INVENTORY.BRONZE.PRODUCTS_RAW;

  -- ---- CRM customers, and their invoices ------------------------------------
  i := 0;
  WHILE (i < NUM_CUSTOMERS) DO
    SELECT DPM_SRC_CRM.BRONZE.SEQ_CUSTOMER_ID.NEXTVAL INTO :new_cust_id;

    INSERT INTO DPM_SRC_CRM.BRONZE.CUSTOMERS_RAW (RAW_PAYLOAD)
    SELECT OBJECT_CONSTRUCT(
      'customer_id', :new_cust_id,
      'full_name', 'Customer ' || :new_cust_id,
      'email', 'customer' || :new_cust_id || '@example.com',
      'signup_date', TO_VARCHAR(DATEADD(day, -UNIFORM(1, 365, RANDOM()), CURRENT_DATE()))
    );

    j := 0;
    WHILE (j < NUM_INVOICES_PER_CUSTOMER) DO
      INSERT INTO DPM_SRC_BILLING.BRONZE.INVOICES_RAW (RAW_PAYLOAD)
      SELECT OBJECT_CONSTRUCT(
        'invoice_id', DPM_SRC_BILLING.BRONZE.SEQ_INVOICE_ID.NEXTVAL,
        'customer_id', :new_cust_id,
        -- Picked from the products that actually exist, so a failing
        -- referential-integrity check means the pipeline lost the join rather
        -- than that the generator invented a product id.
        'product_id', (
          SELECT p.RAW_PAYLOAD:product_id::NUMBER
          FROM DPM_SRC_INVENTORY.BRONZE.PRODUCTS_RAW p
          ORDER BY RANDOM() LIMIT 1
        ),
        'amount', ROUND(UNIFORM(10, 1000, RANDOM()) + UNIFORM(0, 99, RANDOM()) / 100.0, 2),
        'status', ARRAY_CONSTRUCT('PAID','PENDING','OVERDUE')[UNIFORM(0, 2, RANDOM())],
        'invoice_date', TO_VARCHAR(DATEADD(day, -UNIFORM(0, 90, RANDOM()), CURRENT_DATE()))
      );
      invoices_written := invoices_written + 1;
      j := j + 1;
    END WHILE;

    i := i + 1;
  END WHILE;

  -- ---- loyalty members, drawn from existing customers -----------------------
  -- This is the line that makes the identity layer worth having. About half of
  -- the new customers also enrol in loyalty, under the same email, so the
  -- resolver has something real to match on.
  SELECT COALESCE(MAX(SEQ_NO), 0) INTO :next_seq FROM DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW;

  MERGE INTO DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW tgt
  USING (
    SELECT
        c.RAW_PAYLOAD:customer_id::NUMBER AS MEMBER_ID,
        'I' AS OP,
        :next_seq + ROW_NUMBER() OVER (ORDER BY c.RECORD_ID) AS SEQ_NO,
        OBJECT_CONSTRUCT(
          'member_id', c.RAW_PAYLOAD:customer_id::NUMBER,
          'full_name', c.RAW_PAYLOAD:full_name::STRING,
          -- The same address the CRM holds. Identity resolution matches on the
          -- normalised form, so this is what links the two source records.
          'email', c.RAW_PAYLOAD:email::STRING,
          'tier', ARRAY_CONSTRUCT('BRONZE','SILVER','GOLD','PLATINUM')[UNIFORM(0, 3, RANDOM())],
          'status', 'ACTIVE',
          'enrolled_date', TO_VARCHAR(CURRENT_DATE())
        ) AS RAW_PAYLOAD
    FROM DPM_SRC_CRM.BRONZE.CUSTOMERS_RAW c
    WHERE NOT EXISTS (
      SELECT 1 FROM DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW m
      WHERE m.MEMBER_ID = c.RAW_PAYLOAD:customer_id::NUMBER
    )
      AND UNIFORM(0, 1, RANDOM()) = 1
  ) src
  ON tgt.MEMBER_ID = src.MEMBER_ID
  WHEN NOT MATCHED THEN INSERT (MEMBER_ID, OP, SEQ_NO, RAW_PAYLOAD)
    VALUES (src.MEMBER_ID, src.OP, src.SEQ_NO, src.RAW_PAYLOAD);

  members_written := SQLROWCOUNT;
  SELECT COUNT(*) INTO :existing_members FROM DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW;
  SELECT COALESCE(MAX(SEQ_NO), 0) INTO :next_seq FROM DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW;

  -- ---- loyalty CDC updates --------------------------------------------------
  IF (existing_members > 0) THEN
    i := 0;
    WHILE (i < NUM_LOYALTY_UPDATES) DO
      next_seq := next_seq + 1;
      MERGE INTO DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW tgt
      USING (
        SELECT
            m.MEMBER_ID AS MEMBER_ID, 'U' AS OP, :next_seq AS SEQ_NO,
            OBJECT_INSERT(
              OBJECT_INSERT(m.RAW_PAYLOAD, 'tier',
                ARRAY_CONSTRUCT('BRONZE','SILVER','GOLD','PLATINUM')[UNIFORM(0, 3, RANDOM())], TRUE),
              'status',
              ARRAY_CONSTRUCT('ACTIVE','ACTIVE','LAPSED','SUSPENDED')[UNIFORM(0, 3, RANDOM())], TRUE
            ) AS RAW_PAYLOAD
        FROM DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW m
        WHERE m.OP <> 'D'
        ORDER BY RANDOM() LIMIT 1
      ) src
      ON tgt.MEMBER_ID = src.MEMBER_ID
      WHEN MATCHED THEN UPDATE SET
        OP = src.OP, SEQ_NO = src.SEQ_NO, RAW_PAYLOAD = src.RAW_PAYLOAD,
        ETL_UPDATED_AT = CURRENT_TIMESTAMP();
      i := i + 1;
    END WHILE;

    -- ---- loyalty CDC deletes ------------------------------------------------
    -- The tombstone path. Without one the OP <> 'D' filter the MERGE-on-PK
    -- design turns on is never exercised, and a filter nobody exercises is a
    -- filter nobody knows is wrong.
    i := 0;
    WHILE (i < NUM_LOYALTY_DELETES) DO
      next_seq := next_seq + 1;
      MERGE INTO DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW tgt
      USING (
        SELECT m.MEMBER_ID AS MEMBER_ID, 'D' AS OP, :next_seq AS SEQ_NO
        FROM DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW m
        WHERE m.OP <> 'D'
        ORDER BY RANDOM() LIMIT 1
      ) src
      ON tgt.MEMBER_ID = src.MEMBER_ID
      WHEN MATCHED THEN UPDATE SET
        -- The payload is left alone. A delete says the entity is gone, not
        -- that its attributes changed.
        OP = src.OP, SEQ_NO = src.SEQ_NO, ETL_UPDATED_AT = CURRENT_TIMESTAMP();
      i := i + 1;
    END WHILE;
  END IF;

  -- ---- daily point snapshots -----------------------------------------------
  INSERT INTO DPM_SRC_LOYALTY.BRONZE.POINTS_SNAPSHOT_RAW (MEMBER_ID, SNAPSHOT_DATE, RAW_PAYLOAD)
  SELECT
    m.MEMBER_ID,
    d.SNAPSHOT_DATE,
    OBJECT_CONSTRUCT(
      'member_id', m.MEMBER_ID,
      'snapshot_date', TO_VARCHAR(d.SNAPSHOT_DATE),
      'points_earned', UNIFORM(0, 500, RANDOM()),
      'points_redeemed', UNIFORM(0, 200, RANDOM()),
      'channel', ARRAY_CONSTRUCT('APP','WEB','STORE','PARTNER')[UNIFORM(0, 3, RANDOM())]
    )
  FROM DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW m
  CROSS JOIN (
    SELECT DATEADD(day, -SEQ4(), CURRENT_DATE()) AS SNAPSHOT_DATE
    FROM TABLE(GENERATOR(ROWCOUNT => 400))
    -- Bound with `:`, not bare. A procedure argument referenced inside a SQL
    -- statement is a bind variable; without the colon Snowflake resolves it as
    -- a column name and the whole INSERT fails to compile.
    QUALIFY ROW_NUMBER() OVER (ORDER BY SNAPSHOT_DATE DESC) <= :NUM_SNAPSHOT_DAYS
  ) d
  WHERE m.OP <> 'D'
    -- Idempotent per (member, day). Re-running must not double the series, or
    -- the reconciliation against BI fails on the generator rather than on the
    -- pipeline.
    AND NOT EXISTS (
      SELECT 1 FROM DPM_SRC_LOYALTY.BRONZE.POINTS_SNAPSHOT_RAW p
      WHERE p.MEMBER_ID = m.MEMBER_ID AND p.SNAPSHOT_DATE = d.SNAPSHOT_DATE
    );

  snapshot_rows := SQLROWCOUNT;

  -- ---- loader accounting ----------------------------------------------------
  MERGE INTO DPM_SRC_LOYALTY.BRONZE.CURSOR_STATE tgt
  USING (
    SELECT 'members' AS TABLE_NAME, :members_written AS N
    UNION ALL SELECT 'points_snapshot', :snapshot_rows
  ) src
  ON tgt.TABLE_NAME = src.TABLE_NAME
  WHEN MATCHED THEN UPDATE SET
    LAST_CURSOR = TO_VARCHAR(CURRENT_DATE()),
    PAGES_LOADED = COALESCE(tgt.PAGES_LOADED, 0) + 1,
    ROWS_LOADED = COALESCE(tgt.ROWS_LOADED, 0) + src.N,
    STATUS = 'COMPLETED', UPDATED_AT = CURRENT_TIMESTAMP()
  WHEN NOT MATCHED THEN INSERT (TABLE_NAME, LAST_CURSOR, PAGES_LOADED, ROWS_LOADED, STATUS, UPDATED_AT)
    VALUES (src.TABLE_NAME, TO_VARCHAR(CURRENT_DATE()), 1, src.N, 'COMPLETED', CURRENT_TIMESTAMP());

  RETURN 'Wrote ' || NUM_PRODUCTS || ' product(s), ' || NUM_CUSTOMERS || ' customer(s), '
      || invoices_written || ' invoice(s), ' || members_written || ' loyalty enrolment(s) and '
      || snapshot_rows || ' point snapshot row(s).';
END;
$$;

CREATE OR REPLACE TASK DPM_CUSTOMER_360.GOLD.TASK_GENERATE_TEST_DATA
  WAREHOUSE = DPM_PIPELINE_WH
  SCHEDULE = '60 MINUTE'
  COMMENT = 'Hourly test-data generator keeping the whole bronze -> silver -> gold -> BI pipeline exercised'
AS
CALL DPM_CUSTOMER_360.GOLD.SP_GENERATE_TEST_DATA(3, 2, 2, 2, 1, 7);
