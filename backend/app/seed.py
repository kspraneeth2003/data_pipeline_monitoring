"""Seeds one Snowflake connector, two data-product projects, the databases each
one spans, and the example checks that run against the bronze/silver/gold test
pipeline. Run with `python -m app.seed`. Safe to re-run - each definition is
kept in sync with what's below, and configured connector credentials are
preserved."""

from app import models
from app.checks.stage import stage_for
from app.checks.versions import record_version
from app.config import settings
from app.db import SessionLocal
from app.models import cuid


def upsert_connector(db, project, name: str, type_: str, config: dict, comment: str) -> models.Connector:
    connector = db.query(models.Connector).filter_by(project_id=project.id, name=name).first()
    if connector:
        # Only fill in blanks. Re-seeding must never clobber credentials that
        # were configured through the UI/API - the seed's env-var defaults are
        # usually empty, so overwriting here silently breaks every check.
        merged = {**config, **{k: v for k, v in (connector.config or {}).items() if v not in (None, "")}}
        connector.config = merged
        connector.comment = comment
    else:
        connector = models.Connector(
            id=cuid(), project_id=project.id, name=name, type=type_, config=config, comment=comment
        )
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


def upsert_database(db, project, name: str, connector_id: str, description: str) -> models.Database:
    database = db.query(models.Database).filter_by(project_id=project.id, name=name).first()
    if database:
        database.description = description
        database.connector_id = connector_id
    else:
        database = models.Database(
            id=cuid(),
            project_id=project.id,
            connector_id=connector_id,
            name=name,
            slug=name.lower().replace("_", "-"),
            description=description,
        )
        db.add(database)
    db.commit()
    db.refresh(database)
    return database


def upsert_check(db, id_: str, **fields) -> None:
    check = db.query(models.Check).filter_by(id=id_).first()
    if check:
        for key, value in fields.items():
            setattr(check, key, value)
    else:
        check = models.Check(id=id_, **fields)
        db.add(check)

    # Derived from the config every time rather than written into the seed
    # literals: the seed is the standing test of the derivation rules, and a
    # hand-written stage there would hide the case where they are wrong.
    check.stage = stage_for(check.type, check.config, check.stage, bool(check.stage_locked))
    db.flush()
    record_version(db, check, author="seed", note="Seeded")
    db.commit()


def main() -> None:
    db = SessionLocal()
    try:
        # A project is a data product, not a database - it spans the databases
        # that together serve one domain. Customer 360 is fed by the CRM and
        # billing sources and lands in its own gold database.
        customer_360 = upsert_project(
            db, "customer-360", "Customer 360",
            "The customer data product: CRM and billing sources feeding the gold 360 view.",
        )
        inventory_360 = upsert_project(
            db, "inventory-360", "Inventory 360",
            "The inventory data product: product and stock snapshots feeding the gold stock summary.",
        )

        env_config = {
            "account": settings.snowflake_account or "",
            "username": settings.snowflake_user or "",
            "role": settings.snowflake_role or "",
            "warehouse": settings.snowflake_warehouse or "",
        }
        # One connection per project - each project is self-contained, so
        # deleting one can never break another.
        snowflake = upsert_connector(
            db, customer_360, "snowflake-default", "SNOWFLAKE", dict(env_config),
            "Snowflake connection for this project.",
        )
        inventory_snowflake = upsert_connector(
            db, inventory_360, "snowflake-default", "SNOWFLAKE", dict(env_config),
            "Snowflake connection for this project.",
        )

        crm = upsert_database(
            db, customer_360, "DPM_SRC_CRM", snowflake.id,
            "Bronze landing and silver cleaned customer records from the CRM.",
        )
        billing = upsert_database(
            db, customer_360, "DPM_SRC_BILLING", snowflake.id,
            "Bronze landing and silver cleaned invoice records.",
        )
        customer_gold = upsert_database(
            db, customer_360, "DPM_CUSTOMER_360", snowflake.id,
            "Gold: the joined customer 360 view.",
        )
        inventory = upsert_database(
            db, inventory_360, "DPM_SRC_INVENTORY", inventory_snowflake.id,
            "Bronze landing and silver cleaned product/stock snapshots.",
        )
        upsert_database(
            db, inventory_360, "DPM_INVENTORY_360", inventory_snowflake.id,
            "Gold: the stock summary.",
        )

        upsert_check(
            db,
            "seed-row-count-silver-vs-gold",
            database_id=crm.id,
            name="Customers: Silver vs Gold row count parity",
            description="Every customer landed in CRM silver should show up in the gold 360 table.",
            rationale=(
                "The gold 360 table is built by joining CRM silver to billing, and the join is "
                "meant to preserve every customer. A count that drops means the join turned "
                "inner somewhere, or the upstream task did not run - both of which present as "
                "a silently smaller gold table rather than as an error."
            ),
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
            database_id=customer_gold.id,
            name="Gold Customer 360 freshness",
            description="Gold table should be refreshed at least every 90 minutes given the hourly dummy-data generator.",
            rationale=(
                "The generator writes hourly, so 90 minutes is one missed cycle plus slack. "
                "Tighter than that and a single slow run mutes the check; looser and a whole "
                "day of staleness reads as healthy."
            ),
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
            database_id=crm.id,
            name="CRM silver customers: email null rate",
            description="Email should be populated for effectively all customers.",
            rationale=(
                "Email is the key downstream systems match customers on, so a null is not a "
                "missing attribute but an unreachable record. The tolerance is near-zero "
                "because the silver transform is supposed to drop rows without one."
            ),
            type="NULL_RATE",
            schedule="*/15 * * * *",
            connector_id=snowflake.id,
            config={"object": "DPM_SRC_CRM.SILVER.CUSTOMERS", "column": "EMAIL", "maxNullRatio": 0.01},
        )

        upsert_check(
            db,
            "seed-schema-drift-billing-invoices",
            database_id=billing.id,
            name="Billing silver invoices: schema drift",
            description="Guards against unexpected schema changes to the silver invoices table.",
            rationale=(
                "Every parity and quality check on this table is written against its column "
                "contract. When that contract moves, those checks fail all at once and for "
                "reasons that look unrelated - this is the one that says why."
            ),
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
            database_id=crm.id,
            name="CRM bronze -> silver: dedup + parity",
            description=(
                "Every settled bronze customer record should appear exactly once in silver, "
                "with values intact. Key and column mapping mirror TASK_BRONZE_TO_SILVER_CUSTOMERS."
            ),
            rationale=(
                "Silver is meant to be a deduplicated, lossless projection of bronze, so the "
                "comparison is a FULL OUTER JOIN on the MERGE's own key: it can see loss, "
                "surplus and duplication in one pass, where EXCEPT would only see one side. "
                "Value parity is included because key parity alone cannot detect a MERGE that "
                "reads the wrong payload field - the keys still line up perfectly."
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
            database_id=inventory.id,
            name="Inventory bronze -> silver: dedup + parity",
            description=(
                "Same contract for the products pipeline. Currently surfaces the mis-mapped "
                "warehouse_id payload field."
            ),
            rationale=(
                "Written by hand rather than derived, and that is the point: the MERGE reads "
                "RAW_PAYLOAD:warehouse into WAREHOUSE_ID when the landed key is warehouse_id. "
                "A check derived from the MERGE encodes the same mistake and passes. This one "
                "states what the data should be, so it fails."
            ),
            type="BRONZE_TO_SILVER_PARITY",
            schedule="*/10 * * * *",
            connector_id=inventory_snowflake.id,
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

        # Earlier revisions of this seed created one project per source domain,
        # and the projects migration parks pre-existing checks in "Unsorted".
        # Once real projects have claimed every check, those are just noise.
        for stale_slug in ("unsorted", "crm", "inventory", "billing"):
            stale = db.query(models.Project).filter_by(slug=stale_slug).first()
            if stale and not stale.checks:
                db.delete(stale)
        db.commit()

        print("Seed complete.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
