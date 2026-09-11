from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://kotam@localhost:5432/dpm_dev"
    connector_secret_key: str = ""

    # Legacy fallback for the seeded "snowflake-default" connector only.
    snowflake_account: str | None = None
    snowflake_user: str | None = None
    snowflake_role: str | None = None
    snowflake_warehouse: str | None = None
    snowflake_authenticator: str = "SNOWFLAKE_JWT"
    snowflake_private_key_path: str | None = None

    # RCA agent - defaults to shelling out to the Claude Code CLI, no API key needed.
    rca_llm_command: str = "claude"
    anthropic_api_key: str | None = None

    cors_origins: str = "http://localhost:5173"


settings = Settings()
