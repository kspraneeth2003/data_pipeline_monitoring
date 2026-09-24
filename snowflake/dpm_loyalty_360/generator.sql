-- DPM_LOYALTY_360.GOLD - the loyalty test-data generator.
--
-- Kept in its own file rather than beside the gold table because it is not part
-- of the pipeline: it stands in for the loader that would fetch from the
-- loyalty API. The pipeline under test is bronze -> silver -> gold; this is the
-- thing that puts something in bronze for it to move.
--
-- It emits CDC events the way the real source would - an I for a new member, a
-- U when a tier or status changes, a D when one is removed - and upserts them
-- MERGE-on-PK, so bronze holds current entity state rather than an event log.
-- That is the point of this source existing: the other three land append-only,
-- and a parity engine that has only seen append-only bronze has not been tested.
--
-- It also maintains CURSOR_STATE, because the loader's own row accounting is
-- what makes a source-to-bronze question answerable from inside the warehouse.

CREATE OR REPLACE PROCEDURE DPM_LOYALTY_360.GOLD.SP_GENERATE_LOYALTY_DATA(
  "NUM_NEW_MEMBERS" NUMBER,
  "NUM_UPDATES" NUMBER,
  "NUM_SNAPSHOT_DAYS" NUMBER,
  "NUM_DELETES" NUMBER
)
RETURNS VARCHAR
LANGUAGE SQL
COMMENT = 'Emits CDC member events (I/U/D) MERGE-on-PK into bronze, plus daily point snapshots, and maintains CURSOR_STATE'
EXECUTE AS OWNER
AS
$$
DECLARE
  i INTEGER DEFAULT 0;
  new_member_id INTEGER;
  next_seq INTEGER;
  rows_written INTEGER DEFAULT 0;
  snapshot_rows INTEGER DEFAULT 0;
  deleted_rows INTEGER DEFAULT 0;
  existing_members INTEGER;
BEGIN
  -- Sequence numbers are the source's change ordering, so they continue from
  -- whatever is already landed rather than restarting per call. A restart
  -- would make an older change sort above a newer one and the dedup would
  -- pick the wrong version - the exact bug the SEQ_NO column exists to avoid.
  SELECT COALESCE(MAX(SEQ_NO), 0) INTO :next_seq
  FROM DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW;

  -- ---- new members: CDC inserts -------------------------------------------
  WHILE (i < NUM_NEW_MEMBERS) DO
    SELECT DPM_SRC_LOYALTY.BRONZE.SEQ_MEMBER_ID.NEXTVAL INTO :new_member_id;
    next_seq := next_seq + 1;

    MERGE INTO DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW tgt
    USING (
      SELECT
        :new_member_id AS MEMBER_ID,
        'I' AS OP,
        :next_seq AS SEQ_NO,
        OBJECT_CONSTRUCT(
          'member_id', :new_member_id,
          'full_name', 'Member ' || :new_member_id,
          'email', 'member' || :new_member_id || '@example.com',
          'tier', ARRAY_CONSTRUCT('BRONZE','SILVER','GOLD','PLATINUM')[UNIFORM(0, 3, RANDOM())],
          'status', 'ACTIVE',
          'enrolled_date', TO_VARCHAR(DATEADD(day, -UNIFORM(1, 730, RANDOM()), CURRENT_DATE()))
        ) AS RAW_PAYLOAD
    ) src
    ON tgt.MEMBER_ID = src.MEMBER_ID
    WHEN MATCHED THEN UPDATE SET
      OP = src.OP, SEQ_NO = src.SEQ_NO, RAW_PAYLOAD = src.RAW_PAYLOAD,
      ETL_UPDATED_AT = CURRENT_TIMESTAMP()
    WHEN NOT MATCHED THEN INSERT (MEMBER_ID, OP, SEQ_NO, RAW_PAYLOAD)
      VALUES (src.MEMBER_ID, src.OP, src.SEQ_NO, src.RAW_PAYLOAD);

    rows_written := rows_written + 1;
    i := i + 1;
  END WHILE;

  -- ---- existing members: CDC updates --------------------------------------
  -- An update rewrites the entity's single bronze row in place. The previous
  -- payload is gone from bronze - that is what MERGE-on-PK means, and it is
  -- why silver has to be the layer that keeps history here.
  SELECT COUNT(*) INTO :existing_members FROM DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW;

  IF (existing_members > 0) THEN
    i := 0;
    WHILE (i < NUM_UPDATES) DO
      next_seq := next_seq + 1;

      MERGE INTO DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW tgt
      USING (
        SELECT
          m.MEMBER_ID AS MEMBER_ID,
          'U' AS OP,
          :next_seq AS SEQ_NO,
          OBJECT_INSERT(
            OBJECT_INSERT(m.RAW_PAYLOAD, 'tier',
              ARRAY_CONSTRUCT('BRONZE','SILVER','GOLD','PLATINUM')[UNIFORM(0, 3, RANDOM())], TRUE),
            'status',
            ARRAY_CONSTRUCT('ACTIVE','ACTIVE','LAPSED','SUSPENDED')[UNIFORM(0, 3, RANDOM())], TRUE
          ) AS RAW_PAYLOAD
        FROM DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW m
        WHERE m.OP <> 'D'
        ORDER BY RANDOM()
        LIMIT 1
      ) src
      ON tgt.MEMBER_ID = src.MEMBER_ID
      WHEN MATCHED THEN UPDATE SET
        OP = src.OP, SEQ_NO = src.SEQ_NO, RAW_PAYLOAD = src.RAW_PAYLOAD,
        ETL_UPDATED_AT = CURRENT_TIMESTAMP();

      rows_written := rows_written + 1;
      i := i + 1;
    END WHILE;
  END IF;

  -- ---- existing members: CDC deletes --------------------------------------
  -- The tombstone path. Without one of these the OP <> 'D' filter that the
  -- whole MERGE-on-PK design turns on is never exercised, and a filter that is
  -- never exercised is a filter nobody knows is wrong.
  IF (existing_members > 0) THEN
    i := 0;
    WHILE (i < NUM_DELETES) DO
      next_seq := next_seq + 1;

      MERGE INTO DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW tgt
      USING (
        SELECT m.MEMBER_ID AS MEMBER_ID, 'D' AS OP, :next_seq AS SEQ_NO
        FROM DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW m
        WHERE m.OP <> 'D'
        ORDER BY RANDOM()
        LIMIT 1
      ) src
      ON tgt.MEMBER_ID = src.MEMBER_ID
      WHEN MATCHED THEN UPDATE SET
        OP = src.OP, SEQ_NO = src.SEQ_NO,
        -- The payload is left as it was. A delete says the entity is gone, not
        -- that its attributes changed, and blanking it would lose the last
        -- known state that silver's closed version is supposed to preserve.
        ETL_UPDATED_AT = CURRENT_TIMESTAMP();

      deleted_rows := deleted_rows + 1;
      rows_written := rows_written + 1;
      i := i + 1;
    END WHILE;
  END IF;

  -- ---- daily point snapshots ----------------------------------------------
  -- One row per (member, day) for the trailing N days. Append-only: a day's
  -- figures are captured once and the series is the thing of value.
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
    QUALIFY ROW_NUMBER() OVER (ORDER BY SNAPSHOT_DATE DESC) <= NUM_SNAPSHOT_DAYS
  ) d
  WHERE m.OP <> 'D'
    -- Idempotent per (member, day): re-running the generator on the same day
    -- must not double the series, or the reconciliation check against gold
    -- would fail on the generator rather than on the pipeline.
    AND NOT EXISTS (
      SELECT 1 FROM DPM_SRC_LOYALTY.BRONZE.POINTS_SNAPSHOT_RAW p
      WHERE p.MEMBER_ID = m.MEMBER_ID AND p.SNAPSHOT_DATE = d.SNAPSHOT_DATE
    );

  snapshot_rows := SQLROWCOUNT;

  -- ---- loader accounting ---------------------------------------------------
  MERGE INTO DPM_SRC_LOYALTY.BRONZE.CURSOR_STATE tgt
  USING (
    SELECT 'members' AS TABLE_NAME, :rows_written AS N
    UNION ALL
    SELECT 'points_snapshot', :snapshot_rows
  ) src
  ON tgt.TABLE_NAME = src.TABLE_NAME
  WHEN MATCHED THEN UPDATE SET
    LAST_CURSOR = TO_VARCHAR(CURRENT_DATE()),
    PAGES_LOADED = COALESCE(tgt.PAGES_LOADED, 0) + 1,
    ROWS_LOADED = COALESCE(tgt.ROWS_LOADED, 0) + src.N,
    STATUS = 'COMPLETED',
    UPDATED_AT = CURRENT_TIMESTAMP()
  WHEN NOT MATCHED THEN INSERT (TABLE_NAME, LAST_CURSOR, PAGES_LOADED, ROWS_LOADED, STATUS, UPDATED_AT)
    VALUES (src.TABLE_NAME, TO_VARCHAR(CURRENT_DATE()), 1, src.N, 'COMPLETED', CURRENT_TIMESTAMP());

  RETURN 'Wrote ' || rows_written || ' member CDC event(s) (' || deleted_rows
      || ' delete(s)) and ' || snapshot_rows
      || ' point snapshot row(s) into DPM_SRC_LOYALTY.BRONZE. '
      || 'The 1-minute tasks will propagate them to SILVER then GOLD.';
END;
$$;

CREATE OR REPLACE TASK DPM_LOYALTY_360.GOLD.TASK_GENERATE_LOYALTY_DATA
  WAREHOUSE = DPM_PIPELINE_WH
  SCHEDULE = '60 MINUTE'
  COMMENT = 'Hourly loyalty test-data generator: CDC member events plus a trailing week of point snapshots'
AS
CALL DPM_LOYALTY_360.GOLD.SP_GENERATE_LOYALTY_DATA(3, 2, 7, 1);
