from pathlib import Path

import snowflake.connector

from app.config import settings
from app.crypto import decrypt_secret, is_encrypted_secret
from app.connectors.base import SchemaInfo


def _decrypt_if_present(value: str | None) -> str | None:
    if not value:
        return None
    return decrypt_secret(value) if is_encrypted_secret(value) else value


def _read_legacy_private_key() -> str | None:
    if not settings.snowflake_private_key_path:
        return None
    return Path(settings.snowflake_private_key_path).read_text()


def _connection_kwargs(config: dict) -> dict:
    # Everything needed to reach ANY Snowflake account lives in `config`, so
    # the app is account-agnostic - each Connector row is independent.
    # Legacy fallback (env vars) only applies to the original seeded
    # "snowflake-default" connector.
    account = config.get("account") or settings.snowflake_account
    username = config.get("username") or settings.snowflake_user
    if not account or not username:
        raise ValueError("This connector is missing an account/username - edit it to add credentials.")

    authenticator = config.get("authenticator") or settings.snowflake_authenticator
    private_key_pem = _decrypt_if_present(config.get("privateKey")) or _read_legacy_private_key()
    password = _decrypt_if_present(config.get("password"))

    kwargs: dict = {
        "account": account,
        "user": username,
        "role": config.get("role") or settings.snowflake_role,
        "warehouse": config.get("warehouse") or settings.snowflake_warehouse,
        "database": config.get("database"),
        "schema": config.get("schema"),
    }

    if authenticator == "SNOWFLAKE_JWT" and private_key_pem:
        from cryptography.hazmat.primitives import serialization

        private_key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
        kwargs["private_key"] = private_key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    elif password:
        kwargs["password"] = password
    else:
        raise ValueError("This connector has no password or private key configured.")

    return kwargs


class SnowflakeConnector:
    type = "SNOWFLAKE"

    def __init__(self, config: dict):
        self.config = config
        self._conn: snowflake.connector.SnowflakeConnection | None = None

    def _get_connection(self) -> snowflake.connector.SnowflakeConnection:
        if self._conn is None:
            self._conn = snowflake.connector.connect(**_connection_kwargs(self.config))
        return self._conn

    def run_query(self, sql: str) -> list[dict]:
        conn = self._get_connection()
        cur = conn.cursor(snowflake.connector.DictCursor)
        try:
            cur.execute(sql)
            return cur.fetchall()
        finally:
            cur.close()

    def get_schema(self, object_name: str) -> SchemaInfo:
        rows = self.run_query(f"DESCRIBE TABLE {object_name}")
        return {
            "object": object_name,
            "columns": [
                {
                    "name": str(r["name"]),
                    "data_type": str(r["type"]),
                    "nullable": str(r["null?"]).upper() != "N",
                }
                for r in rows
            ],
        }

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def test_snowflake_connection(config: dict) -> None:
    connector = SnowflakeConnector(config)
    try:
        connector.run_query("SELECT 1")
    finally:
        connector.close()


def probe_snowflake(config: dict) -> dict:
    """Test credentials and report what they can reach, without saving anything.

    This is what lets a project be set up in one pass: the user types credentials,
    sees which account/role they actually landed on and which databases exist,
    and picks from that - instead of saving a connector, navigating elsewhere,
    and finding out later that the role cannot see what they expected.
    """
    connector = SnowflakeConnector(config)
    try:
        identity = connector.run_query(
            "SELECT CURRENT_ACCOUNT() AS ACCOUNT, CURRENT_USER() AS USERNAME, "
            "CURRENT_ROLE() AS ROLE, CURRENT_WAREHOUSE() AS WAREHOUSE"
        )[0]
        rows = connector.run_query("SHOW DATABASES")
        names = sorted(
            str(r["name"])
            for r in rows
            if r.get("name") and str(r["name"]) not in {"SNOWFLAKE", "SNOWFLAKE_SAMPLE_DATA"}
        )
        return {
            "account": identity.get("ACCOUNT"),
            "username": identity.get("USERNAME"),
            "role": identity.get("ROLE"),
            "warehouse": identity.get("WAREHOUSE"),
            "databases": names,
        }
    finally:
        connector.close()
