"""The one place a model provider is named.

Both agents go through `init_chat_model`, so the provider is a string in
`.env` rather than an import anywhere in the codebase:

    AGENT_MODEL=openai:gpt-5
    AGENT_MODEL=anthropic:claude-opus-5
    AGENT_MODEL=ollama:llama3

Switching providers means installing `langchain-<provider>` and editing that
line. Nothing in `monitoring/` or `maintenance/` knows which one is in use,
for the same reason `checks/engine.py` does not know which warehouse it is
talking to: the moment provider-specific behaviour leaks past this module,
the abstraction has failed and swapping becomes a rewrite.

`agent_model()` returns None rather than raising when nothing is configured,
because every caller already has a rule-based path and "no model" is a
supported way to run this system, not an error.

One thing this module deliberately does *not* do is hide tool-calling
support. An agent needs a model that can emit structured tool calls, and a
model that cannot will fail at bind time with a clear provider error. Wrapping
that in a friendlier message would only delay the discovery to a point where
it is harder to diagnose.
"""

import logging
from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from app.config import settings

logger = logging.getLogger("dpm.agents")


class ModelUnavailable(RuntimeError):
    """The configured model could not be constructed.

    Raised only from `require_model`, for callers that have already decided
    an agent is expected. The sweep catches it and falls back.
    """


# Selects the local Claude Code CLI instead of a hosted provider. Accepts a
# bare "claude-cli" or "claude-cli:<model>" to pin the CLI's own model.
CLI_PREFIX = "claude-cli"


@lru_cache(maxsize=1)
def agent_model() -> BaseChatModel | None:
    """The configured chat model, or None if agents are switched off.

    Cached: constructing a client per sweep would rebuild an HTTP pool every
    five minutes for no benefit. Cleared by `reset_model_cache` in tests.
    """
    if not settings.agent_enabled:
        return None

    configured = settings.agent_model.strip()
    if configured == CLI_PREFIX or configured.startswith(f"{CLI_PREFIX}:"):
        # Not routed through `init_chat_model`: this is a local subprocess,
        # not a provider integration, and there is no package to install or
        # credential to read. It still satisfies the same BaseChatModel
        # contract, so nothing downstream can tell the difference.
        from app.agents.claude_cli import ClaudeCliChatModel

        _, _, cli_model = configured.partition(":")
        logger.info(
            "Agent model ready: local Claude Code CLI%s",
            f" ({cli_model})" if cli_model else "",
        )
        return ClaudeCliChatModel(
            command=settings.rca_llm_command,
            cli_model=cli_model or None,
            timeout_seconds=settings.agent_cli_timeout_seconds,
        )

    try:
        model = init_chat_model(
            configured,
            temperature=settings.agent_temperature,
        )
    except Exception:
        # Almost always a missing `langchain-<provider>` package or an unset
        # API key. Logged with the traceback because the provider's own
        # message is far more useful than anything this layer could invent.
        logger.exception(
            "Could not construct agent model %r - falling back to rules",
            settings.agent_model,
        )
        return None
    logger.info("Agent model ready: %s", settings.agent_model)
    return model


def require_model() -> BaseChatModel:
    model = agent_model()
    if model is None:
        raise ModelUnavailable(
            f"No usable agent model (AGENT_MODEL={settings.agent_model!r})"
        )
    return model


def reset_model_cache() -> None:
    agent_model.cache_clear()
