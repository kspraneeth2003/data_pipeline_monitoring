# Maps a Snowflake "DATABASE.SCHEMA" prefix to the tracked SQL file that
# defines it in the repo (see ../../../snowflake/). Static config for now -
# a real deployment would likely source this from dbt `meta` tags or a catalog.
OBJECT_TO_PATH: dict[str, str] = {
    "DPM_SRC_CRM.BRONZE": "snowflake/dpm_src_crm/bronze.sql",
    "DPM_SRC_CRM.SILVER": "snowflake/dpm_src_crm/silver.sql",
    "DPM_SRC_BILLING.BRONZE": "snowflake/dpm_src_billing/bronze.sql",
    "DPM_SRC_BILLING.SILVER": "snowflake/dpm_src_billing/silver.sql",
    "DPM_CUSTOMER_360.GOLD": "snowflake/dpm_customer_360/gold.sql",
    "DPM_SRC_INVENTORY.BRONZE": "snowflake/dpm_src_inventory/bronze.sql",
    "DPM_SRC_INVENTORY.SILVER": "snowflake/dpm_src_inventory/silver.sql",
    "DPM_INVENTORY_360.GOLD": "snowflake/dpm_inventory_360/gold.sql",
}


def resolve_repo_path(fully_qualified_object: str) -> str | None:
    parts = fully_qualified_object.split(".")
    if len(parts) < 2:
        return None
    prefix = f"{parts[0]}.{parts[1]}".upper()
    return OBJECT_TO_PATH.get(prefix)
