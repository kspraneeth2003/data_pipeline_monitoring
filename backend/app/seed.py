"""Seeds one Snowflake connector, three projects, and the example checks that
run against the bronze/silver/gold test pipeline. Run with `python -m app.seed`.
Safe to re-run - each definition is kept in sync with what's below, and
configured connector credentials are preserved."""

from app import models
from app.config import settings
from app.db import SessionLocal
from app.models import cuid


def upsert_connector(db, name: str, type_: str, config: dict, comment: str) -> models.Connector:
    connector = db.query(models.Connector).filter_by(name=name).first()
    if connector:
        # Only fill in blanks. Re-seeding must never clobber credentials that
        # were configured through the UI/API - the seed's env-var defaults are
        # usually empty, so overwriting here silently breaks every check.
        merged = {**config, **{k: v for k, v in (connector.config or {}).items() if v not in (None, "")}}
        connector.config = merged
        connector.comment = comment
    else:
        connector = models.Connector(id=cuid(), name=name, type=type_, config=config, comment=comment)
        db.add(connector)
    db.commit()
    db.refresh(connector)
    return connector


def upsert_project(db, slug: str, name: str, description: str) -> models.Project:
    project = db.query(models.Project).filter_by(slug=slug).first()
    if project:
        project.name = name
        project.description = description
    else:
        project = models.Project(id=cuid(), slug=slug, name=name, description=description)
        db.add(project)
    db.commit()
    db.refresh(project)
    return project


def upsert_check(db, id_: str, **fields) -> None:
    check = db.query(models.Check).filter_by(id=id_).first()
    if check:
        for key, value in fields.items():
            setattr(check, key, value)
    else:
        db.add(models.Check(id=id_, **fields))
    db.commit()


def main() -> None:
    db = SessionLocal()
    try:
        snowflake = upsert_connector(
            db,
            "snowflake-default",
            "SNOWFLAKE",
            {
                "account": settings.snowflake_account or "",
                "username": settings.snowflake_user or "",
                "role": settings.snowflake_role or "",
                "warehouse": settings.snowflake_warehouse or "",
            },
            "Default Snowflake connection (key-pair auth via legacy .env fallback).",
        )

        # One project per source domain. This is the grouping a data team
        # actually reasons about: "is CRM healthy", not "are checks 1-6 green".
        crm = upsert_project(
            db, "crm", "CRM",
            "Customer records landing from the CRM into bronze, cleaned into silver, joined into the gold 360 view.",
        )
        inventory = upsert_project(
            db, "inventory", "Inventory",
            "Product and stock snapshots from the inventory system.",
        )
        billing = upsert_project(
            db, "billing", "Billing",
            "Invoice records and their downstream billing aggregates.",
        )

        upsert_check(
            db,
            "seed-row-count-silver-vs-gold",
            project_id=crm.id,
            name="Customers: Silver vs Gold row count parity",
            description="Every customer landed in CRM silver should show up in the gold 360 table.",
            type="ROW_COUNT",
            schedule="*/5 * * * *",
            connector_id=snowflake.id,
            config={
                "object": "DPM_SRC_CRM.SILVER.CUSTOMERS",
                "comparisonObject": "DPM_CUSTOMER_360.GOLD.CUSTOMER_360",
                "toleranceAbs": 0,
            },
        )

        upsert_check(
            db,
            "seed-freshness-gold-360",
            project_id=crm.id,
            name="Gold Customer 360 freshness",
            description="Gold table should be refreshed at least every 90 minutes given the hourly dummy-data generator.",
            type="FRESHNESS",
            schedule="*/10 * * * *",
            connector_id=snowflake.id,
            config={
                "object": "DPM_CUSTOMER_360.GOLD.CUSTOMER_360",
                "timestampColumn": "UPDATED_AT",
                "maxAgeMinutes": 90,
            },
        )

        upsert_check(
            db,
            "seed-null-rate-customer-email",
            project_id=crm.id,
            name="CRM silver customers: email null rate",
            description="Email should be populated for effectively all customers.",
            type="NULL_RATE",
            schedule="*/15 * * * *",
            connector_id=snowflake.id,
            config={"object": "DPM_SRC_CRM.SILVER.CUSTOMERS", "column": "EMAIL", "maxNullRatio": 0.01},
        )

        upsert_check(
            db,
            "seed-schema-drift-billing-invoices",
            project_id=billing.id,
            name="Billing silver invoices: schema drift",
            description="Guards against unexpected schema changes to the silver invoices table.",
            type="SCHEMA_DRIFT",
            schedule="*/30 * * * *",
            connector_id=snowflake.id,
            config={
                "object": "DPM_SRC_BILLING.SILVER.INVOICES",
                "expectedColumns": [
                    {"name": "INVOICE_ID", "dataType": "NUMBER"},
                    {"name": "CUSTOMER_ID", "dataType": "NUMBER"},
                    {"name": "AMOUNT", "dataType": "NUMBER"},
                    {"name": "STATUS", "dataType": "VARCHAR"},
                    {"name": "INVOICE_DATE", "dataType": "DATE"},
                    {"name": "UPDATED_AT", "dataType": "TIMESTAMP_NTZ"},
                ],
            },
        )

        # --- Bronze -> Silver parity suite -------------------------------
        # The composite key and the column mapping below are lifted directly
        # from the MERGE in snowflake/dpm_src_crm/bronze.sql. That MERGE is the
        # contract; this check asserts the contract actually held.
        upsert_check(
            db,
            "seed-b2s-crm-customers",
            project_id=crm.id,
            name="CRM bronze -> silver: dedup + parity",
            description=(
                "Every settled bronze customer record should appear exactly once in silver, "
                "with values intact. Key and column mapping mirror TASK_BRONZE_TO_SILVER_CUSTOMERS."
            ),
            type="BRONZE_TO_SILVER_PARITY",
            schedule="*/10 * * * *",
            connector_id=snowflake.id,
            config={
                "bronzeObject": "DPM_SRC_CRM.BRONZE.CUSTOMERS_RAW",
                "silverObject": "DPM_SRC_CRM.SILVER.CUSTOMERS",
                "bronzeLoadedAtColumn": "LOADED_AT",
                "bronzeSequenceColumn": "RECORD_ID",
                "lagMinutes": 5,
                "keyColumns": [
                    {
                        "name": "CUSTOMER_ID",
                        "bronze": "RAW_PAYLOAD:customer_id::NUMBER",
                        "silver": "CUSTOMER_ID",
                    }
                ],
                "valueColumns": [
                    {"name": "FULL_NAME", "bronze": "RAW_PAYLOAD:full_name::STRING", "silver": "FULL_NAME"},
                    {"name": "EMAIL", "bronze": "RAW_PAYLOAD:email::STRING", "silver": "EMAIL"},
                    {"name": "SIGNUP_DATE", "bronze": "RAW_PAYLOAD:signup_date::DATE", "silver": "SIGNUP_DATE"},
                ],
            },
        )

        # This one is expected to FAIL against the current pipeline: the MERGE in
        # snowflake/dpm_src_inventory/bronze.sql reads RAW_PAYLOAD:warehouse when
        # the landed payload key is warehouse_id, so WAREHOUSE_ID is NULL for every
        # silver row. Key parity is perfect, which is exactly why the value-level
        # comparison is what catches it.
        upsert_check(
            db,
            "seed-b2s-inventory-products",
            project_id=inventory.id,
            name="Inventory bronze -> silver: dedup + parity",
            description=(
                "Same contract for the products pipeline. Currently surfaces the mis-mapped "
                "warehouse_id payload field."
            ),
            type="BRONZE_TO_SILVER_PARITY",
            schedule="*/10 * * * *",
            connector_id=snowflake.id,
            config={
                "bronzeObject": "DPM_SRC_INVENTORY.BRONZE.PRODUCTS_RAW",
                "silverObject": "DPM_SRC_INVENTORY.SILVER.PRODUCTS",
                "bronzeLoadedAtColumn": "LOADED_AT",
                "bronzeSequenceColumn": "RECORD_ID",
                "lagMinutes": 5,
                "keyColumns": [
                    {
                        "name": "PRODUCT_ID",
                        "bronze": "RAW_PAYLOAD:product_id::NUMBER",
                        "silver": "PRODUCT_ID",
                    }
                ],
                "valueColumns": [
                    {"name": "PRODUCT_NAME", "bronze": "RAW_PAYLOAD:product_name::STRING", "silver": "PRODUCT_NAME"},
                    {"name": "CATEGORY", "bronze": "RAW_PAYLOAD:category::STRING", "silver": "CATEGORY"},
                    {"name": "UNIT_PRICE", "bronze": "RAW_PAYLOAD:unit_price::NUMBER(10,2)", "silver": "UNIT_PRICE"},
                    {
                        "name": "QUANTITY_ON_HAND",
                        "bronze": "RAW_PAYLOAD:quantity_on_hand::NUMBER",
                        "silver": "QUANTITY_ON_HAND",
                    },
                    {"name": "WAREHOUSE_ID", "bronze": "RAW_PAYLOAD:warehouse_id::STRING", "silver": "WAREHOUSE_ID"},
                ],
            },
        )

        # The projects migration parks pre-existing checks in "Unsorted". Once
        # they have been claimed by a real project, an empty placeholder is just
        # noise on the home page.
        unsorted = db.query(models.Project).filter_by(slug="unsorted").first()
        if unsorted and not unsorted.checks:
            db.delete(unsorted)
            db.commit()

        print("Seed complete.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
