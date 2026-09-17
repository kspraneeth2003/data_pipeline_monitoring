import json
import re
import shutil
import subprocess
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.config import settings


def resolve_llm_command(command: str) -> str:
    """Resolves the CLI to a real executable path before exec'ing it.

    On Windows the `claude` install directory holds both an extensionless shim
    and `claude.exe`. `CreateProcess` picks the extensionless one and fails with
    "Access is denied", so passing the bare name raises WinError 5 while the
    identical command works fine from a shell. `shutil.which` applies PATHEXT
    and lands on the executable.

    Falls back to the name as given: if it cannot be resolved, letting the
    subprocess call fail with its own error is clearer than inventing one here.
    """
    return shutil.which(command) or command


class ClaudeCliChatModel(BaseChatModel):
    """A LangChain chat model that shells out to the Claude Code CLI instead of
    calling the Anthropic API - so this works off the same login as whatever
    `claude` session is on PATH, with no ANTHROPIC_API_KEY required.

    Two details are load-bearing:

    `--allowedTools ""` is an empty allowlist: the CLI gets no tool access and
    is a pure text generator, which is what makes it safe to invoke from a
    request handler. It replaces `--restricted`, which this used to pass and
    which no longer exists - the CLI now exits 1 on it, so every LLM call was
    failing and silently falling back to the heuristic.

    The prompt goes over stdin, not argv. `--allowedTools` is variadic and will
    otherwise swallow a prompt that follows it, and Windows caps a command line
    at ~32k characters - well under the size of an evidence block or a parsed
    repository.
    """

    timeout_seconds: int = 45

    @property
    def _llm_type(self) -> str:
        return "claude-cli"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        prompt = "\n\n".join(str(m.content) for m in messages)
        result = subprocess.run(
            [resolve_llm_command(settings.rca_llm_command), "-p", "--allowedTools", ""],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
        )
        if result.returncode != 0:
            raise RuntimeError(f"claude CLI exited {result.returncode}: {result.stderr.strip()}")

        text = result.stdout.strip()
        message = AIMessage(content=text)
        return ChatResult(generations=[ChatGeneration(message=message)])


def extract_json(text: str) -> dict:
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    candidate = fenced.group(1) if fenced else text
    return json.loads(candidate.strip())
