from app.connectors.base import Connector
from app.connectors.snowflake_connector import SnowflakeConnector


def build_connector(connector_type: str, config: dict) -> Connector:
    if connector_type == "SNOWFLAKE":
        return SnowflakeConnector(config)
    if connector_type == "POSTGRES":
        raise NotImplementedError(
            "POSTGRES connector is not implemented yet - this is the cross-platform parity spike (PLAN.md M4)"
        )
    raise ValueError(f"Unknown connector type: {connector_type}")
