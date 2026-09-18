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

    # GitHub App - lets the user grant read access to specific repositories
    # instead of pasting a URL and a personal access token. Optional: with these
    # unset the UI simply offers the paste-a-URL path on its own.
    #
    # The private key is a secret on the same footing as CONNECTOR_SECRET_KEY.
    # Point at the .pem GitHub gives you, or inline it; never commit either.
    github_app_id: str = ""
    github_app_slug: str = ""
    github_app_private_key: str = ""
    github_app_private_key_path: str = ""

    # Where to send the browser back to after GitHub's consent screen. Defaults
    # to the first allowed CORS origin, which is the frontend in every setup so
    # far, so this normally needs no configuration.
    frontend_url: str = ""

    # Where ingested repositories are checked out. These are working copies, not
    # data: deleting the directory costs a re-clone and nothing else.
    repo_cache_dir: str = ".repo-cache"
    ingest_llm_timeout_seconds: int = 180

    cors_origins: str = "http://localhost:5173"

    @property
    def frontend_base_url(self) -> str:
        return self.frontend_url.strip() or self.cors_origins.split(",")[0].strip()


settings = Settings()
