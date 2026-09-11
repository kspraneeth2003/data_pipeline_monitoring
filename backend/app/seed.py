"""Seeds one Snowflake connector and 4 example checks against the
bronze/silver/gold test pipeline. Run with `python -m app.seed`. Safe to
re-run - each definition is kept in sync with what's below."""

from app import models
from app.config import settings
from app.db import SessionLocal
from app.models import cuid


def upsert_connector(db, name: str, type_: str, config: dict, comment: str) -> models.Connector:
    connector = db.query(models.Connector).filter_by(name=name).first()
    if connector:
        connector.config = config
        connector.comment = comment
    else:
        connector = models.Connector(id=cuid(), name=name, type=type_, config=config, comment=comment)
        db.add(connector)
    db.commit()
    db.refresh(connector)
    return connector


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

        upsert_check(
            db,
            "seed-row-count-silver-vs-gold",
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

        print("Seed complete.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
