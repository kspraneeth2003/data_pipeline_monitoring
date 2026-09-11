from typing import TypedDict

from app.connectors.base import Connector

DDL_QUERY_TYPES = [
    "CREATE_TABLE",
    "ALTER_TABLE",
    "CREATE_OR_ALTER_TABLE",
    "DROP_TABLE",
    "CREATE_TASK",
    "ALTER_TASK",
    "CREATE_STREAM",
    "ALTER_STREAM",
]


class DdlEvent(TypedDict):
    query_type: str
    start_time: str
    user_name: str
    query_text: str


class SnowflakeObjectContext(TypedDict):
    object: str
    last_altered: str | None
    created: str | None
    row_count: int | None
    recent_ddl: list[DdlEvent]


def gather_snowflake_context(connector: Connector, fully_qualified_object: str) -> SnowflakeObjectContext:
    """Pulls near-real-time Snowflake metadata for one object via
    INFORMATION_SCHEMA (no ACCOUNT_USAGE latency, but 7-day/current-user
    scoped)."""
    parts = fully_qualified_object.split(".")
    database, schema, table = parts[0], parts[1], parts[2]

    last_altered = created = None
    row_count = None
    try:
        rows = connector.run_query(
            f"SELECT LAST_ALTERED, CREATED, ROW_COUNT FROM {database}.INFORMATION_SCHEMA.TABLES "
            f"WHERE TABLE_SCHEMA = '{schema}' AND TABLE_NAME = '{table}'"
        )
        if rows:
            row = rows[0]
            last_altered = str(row["LAST_ALTERED"]) if row.get("LAST_ALTERED") else None
            created = str(row["CREATED"]) if row.get("CREATED") else None
            row_count = int(row["ROW_COUNT"]) if row.get("ROW_COUNT") is not None else None
    except Exception:
        pass

    recent_ddl: list[DdlEvent] = []
    try:
        type_list = ", ".join(f"'{t}'" for t in DDL_QUERY_TYPES)
        rows = connector.run_query(
            f"""SELECT QUERY_TYPE, START_TIME, USER_NAME, QUERY_TEXT
                FROM TABLE({database}.INFORMATION_SCHEMA.QUERY_HISTORY(
                  END_TIME_RANGE_START => DATEADD('day', -7, CURRENT_TIMESTAMP())
                ))
                WHERE QUERY_TEXT ILIKE '%{table}%'
                  AND QUERY_TYPE IN ({type_list})
                  AND EXECUTION_STATUS = 'SUCCESS'
                ORDER BY START_TIME DESC
                LIMIT 10"""
        )
        recent_ddl = [
            {
                "query_type": str(r["QUERY_TYPE"]),
                "start_time": str(r["START_TIME"]),
                "user_name": str(r["USER_NAME"]),
                "query_text": str(r["QUERY_TEXT"])[:500],
            }
            for r in rows
        ]
    except Exception:
        pass

    return {
        "object": fully_qualified_object,
        "last_altered": last_altered,
        "created": created,
        "row_count": row_count,
        "recent_ddl": recent_ddl,
    }
