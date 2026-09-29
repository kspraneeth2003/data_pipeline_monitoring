"""Telling a broken check apart from a broken pipeline.

The cases are taken from the Customer 360 project, where 7 of 11 derived parity
checks did not compile because MERGE aliases were copied into them - and the
RCA agent then blamed the pipeline's last commit for our SQL.
"""

from app.checks.defects import assess_error, localize_parity_columns, root_columns
from app.ingest.ddl_parser import parse_merges

ALIAS_ERROR = (
    "000904 (42000): 01c764b7: SQL compilation error: error line 8 at position 6 "
    "invalid identifier 'S.MEMBER_ID'"
)
MEMBERS_CONFIG = {
    "bronzeObject": "DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW",
    "silverObject": "DPM_SRC_LOYALTY.SILVER.MEMBERS",
    "bronzeLoadedAtColumn": "ETL_LOADED_AT",
    "bronzeSequenceColumn": "SEQ_NO",
    "keyColumns": [{"name": "MEMBER_ID", "bronze": "s.MEMBER_ID", "silver": "MEMBER_ID"}],
    "valueColumns": [
        {"name": "TIER", "bronze": "s.RAW_PAYLOAD:tier::STRING", "silver": "TIER"},
    ],
}
LIVE = {
    "DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW": {"MEMBER_ID", "RAW_PAYLOAD", "ETL_LOADED_AT", "SEQ_NO", "OP"},
    "DPM_SRC_LOYALTY.SILVER.MEMBERS": {"MEMBER_ID", "TIER", "STATUS", "IS_CURRENT"},
}


def test_source_alias_is_stripped_and_nothing_else_is_touched():
    result = localize_parity_columns(
        MEMBERS_CONFIG["keyColumns"], MEMBERS_CONFIG["valueColumns"], driving_alias="s"
    )
    assert [k["bronze"] for k in result.key_columns] == ["MEMBER_ID"]
    # The VARIANT path and the cast survive; only the qualifier goes.
    assert [v["bronze"] for v in result.value_columns] == ["RAW_PAYLOAD:tier::STRING"]
    assert result.driving_alias == "s" and not result.dropped and not result.blocking_reason


def test_a_value_from_a_joined_table_is_dropped_and_named():
    # GOLD.TRANSACTION: INVOICES i LEFT JOIN INDIVIDUAL_XREF x.
    result = localize_parity_columns(
        [{"name": "TRANSACTION_ID", "bronze": "i.INVOICE_ID", "silver": "TRANSACTION_ID"}],
        [
            {"name": "INDIVIDUAL_ID", "bronze": "x.INDIVIDUAL_ID", "silver": "INDIVIDUAL_ID"},
            {"name": "AMOUNT", "bronze": "i.AMOUNT", "silver": "AMOUNT"},
        ],
        driving_alias="i",
    )
    assert [v["name"] for v in result.value_columns] == ["AMOUNT"]
    assert result.dropped == ["INDIVIDUAL_ID (reads table/CTE alias x)"]


def test_a_key_from_a_joined_table_means_no_check_at_all():
    # GOLD.LOYALTY_DAILY is keyed on the XREF's INDIVIDUAL_ID. Comparing the
    # source table on it cannot be expressed, so no check is better than one
    # that errors - or worse, one that compares something else and passes.
    result = localize_parity_columns(
        [{"name": "INDIVIDUAL_ID", "bronze": "x.INDIVIDUAL_ID", "silver": "INDIVIDUAL_ID"}],
        [],
        driving_alias="p",
    )
    assert result.blocking_reason and "x.INDIVIDUAL_ID" in result.blocking_reason


def test_a_procedure_bind_variable_is_not_a_column():
    result = localize_parity_columns(
        [{"name": "K", "bronze": "c.ID", "silver": "K"}],
        [{"name": "SEQ_NO", "bronze": ":next_seq + ROW_NUMBER() OVER (ORDER BY c.ID)", "silver": "SEQ_NO"}],
        driving_alias="c",
    )
    assert result.value_columns == [] and result.dropped == ["SEQ_NO (reads a procedure variable)"]


def test_the_driving_alias_is_inferred_from_live_columns_when_unknown():
    # Existing checks predate the parser recording the alias; repair infers it.
    result = localize_parity_columns(
        MEMBERS_CONFIG["keyColumns"],
        MEMBERS_CONFIG["valueColumns"],
        source_columns=LIVE["DPM_SRC_LOYALTY.BRONZE.MEMBERS_RAW"],
    )
    assert result.driving_alias == "s"


def test_root_columns_ignores_functions_casts_paths_and_literals():
    assert root_columns("COALESCE(tx.LIFETIME_VALUE, 0)") == {"LIFETIME_VALUE"}
    assert root_columns("RAW_PAYLOAD:amount::NUMBER(12,2)") == {"RAW_PAYLOAD"}
    assert root_columns("CASE WHEN STATUS = 'PAID' THEN 1 END") == {"STATUS"}


def test_the_alias_error_is_a_defect_with_a_repair():
    assessment = assess_error("BRONZE_TO_SILVER_PARITY", MEMBERS_CONFIG, ALIAS_ERROR, LIVE)
    assert assessment.kind == "defect"
    assert assessment.repaired_config["keyColumns"][0]["bronze"] == "MEMBER_ID"
    # What the user reads carries no SQL; the raw error is kept for us.
    assert "SQL compilation" not in assessment.summary
    assert "S.MEMBER_ID" in assessment.diagnostics["error"]


def test_a_dropped_column_is_the_pipeline_moving_not_a_defect():
    # The check is correct; the warehouse no longer has the column it needs.
    # That is real news about the pipeline and must still reach the user.
    config = {"object": "DB.SILVER.CUSTOMERS", "timestampColumn": "UPDATED_AT", "maxAgeMinutes": 60}
    live = {"DB.SILVER.CUSTOMERS": {"CUSTOMER_ID", "EMAIL"}}
    error = "000904 (42000): SQL compilation error: invalid identifier 'UPDATED_AT'"
    assessment = assess_error("FRESHNESS", config, error, live)
    assert assessment.kind == "pipeline"
    assert assessment.diagnostics["missing_columns"] == ["DB.SILVER.CUSTOMERS.UPDATED_AT"]


def test_compile_error_with_every_column_present_is_ours():
    config = {"object": "DB.SILVER.CUSTOMERS", "timestampColumn": "UPDATED_AT", "maxAgeMinutes": 60}
    live = {"DB.SILVER.CUSTOMERS": {"UPDATED_AT"}}
    error = "001003 (42000): SQL compilation error: syntax error line 1 at position 9"
    assert assess_error("FRESHNESS", config, error, live).kind == "defect"


def test_errors_that_are_not_compile_errors_are_left_alone():
    # Credentials, a suspended warehouse, a dropped table: not our SQL.
    for error in (
        "250001: Could not connect to Snowflake backend",
        "002003 (02000): SQL compilation error: Object 'DB.SILVER.X' does not exist or not authorized.",
    ):
        assert assess_error("BRONZE_TO_SILVER_PARITY", MEMBERS_CONFIG, error, LIVE).kind == "pipeline"


def test_an_undescribable_table_is_not_grounds_to_call_it_our_fault():
    config = {"object": "DB.SILVER.CUSTOMERS", "column": "EMAIL", "maxNullRatio": 0.1}
    error = "000904 (42000): SQL compilation error: invalid identifier 'EMAIL'"
    assert assess_error("NULL_RATE", config, error, {"DB.SILVER.CUSTOMERS": None}).kind == "pipeline"


def test_the_parser_records_the_merge_source_alias():
    sql = """
    MERGE INTO DB.SILVER.MEMBERS tgt USING (
      SELECT s.MEMBER_ID AS MEMBER_ID FROM DB.BRONZE.MEMBERS_RAW s WHERE s.OP <> 'D'
    ) src ON tgt.MEMBER_ID = src.MEMBER_ID
    WHEN MATCHED THEN UPDATE SET MEMBER_ID = src.MEMBER_ID;
    MERGE INTO DB.SILVER.ORDERS tgt USING (
      SELECT ORDER_ID FROM DB.BRONZE.ORDERS_RAW WHERE ORDER_ID IS NOT NULL
    ) src ON tgt.ORDER_ID = src.ORDER_ID
    WHEN MATCHED THEN UPDATE SET ORDER_ID = src.ORDER_ID;
    """
    merges = parse_merges(sql, "x.sql")
    # `WHERE` right after the table is a clause, not an alias.
    assert [m["source_alias"] for m in merges] == ["S", None]
