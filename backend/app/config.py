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

    # --- Agents -------------------------------------------------------
    #
    # The monitoring and maintenance agents go through LangChain's
    # `init_chat_model`, so the provider is configuration rather than code:
    # "openai:gpt-5", "anthropic:claude-opus-5", "ollama:llama3" all work,
    # given the matching `langchain-<provider>` package and its API key in
    # this file. Nothing outside `app/agents/model.py` imports a provider.
    #
    # Empty means no agent. Both agents then fall back to their rule-based
    # path, which is the whole point of having one: monitoring keeps working
    # without a model, it just stops exercising judgement.
    agent_model: str = ""
    agent_temperature: float = 0.0
    openai_api_key: str | None = None

    # Ceilings on one agent invocation. An agent that loops is a cost
    # incident, and these are cheaper than discovering it on a bill.
    agent_max_model_calls: int = 12
    agent_max_tool_calls: int = 24
    agent_timeout_seconds: int = 120

    # How often to check whether any project's DDL has moved. Slower than
    # the monitor sweep because it costs a git fetch and a warehouse query
    # per project, and a pipeline definition changes on the order of days.
    maintenance_interval_seconds: int = 900
    # Reading warehouse DDL history needs a live connection per project. Off
    # by default so a fresh install does not open warehouse connections on a
    # timer before anyone has asked it to.
    maintenance_scan_warehouse: bool = False

    # How often the reporting agent sweeps for incidents needing attention.
    # Every run already wakes the sweep through the scheduler; this is the
    # floor on how often the *agent* is consulted, so a burst of failures
    # cannot turn into a burst of model calls.
    monitor_interval_seconds: int = 300
    # An open incident whose ticket has not moved in this long gets escalated.
    escalate_after_hours: float = 24.0
    # ...and not again for at least this long, so escalation does not become
    # a daily nag that gets filtered out.
    escalation_backoff_hours: float = 24.0
    # Most escalations one sweep may issue, oldest first. Without a cap the
    # first sweep over an existing backlog escalates everything at once -
    # observed at 189 on the local database - which is both unreadable and,
    # with a real tracker attached, several hundred API calls in one tick.
    # The rest are not lost, only deferred to the next sweep.
    max_escalations_per_sweep: int = 10

    # --- Jira ---------------------------------------------------------
    #
    # All four are required together. With any missing, tickets go to the
    # in-app board and the app behaves identically otherwise.
    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    jira_project_key: str = ""
    jira_issue_type: str = "Task"
    # The status name to transition to on clear. Per-workflow, and renamed
    # often enough ("Done" -> "Complete") that it has to be configurable.
    jira_done_status: str = "Done"

    @property
    def jira_configured(self) -> bool:
        return all(
            [self.jira_base_url, self.jira_email, self.jira_api_token, self.jira_project_key]
        )

    @property
    def agent_enabled(self) -> bool:
        return bool(self.agent_model.strip())

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
