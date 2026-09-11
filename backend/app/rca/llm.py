import json
import re
import subprocess
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.config import settings


class ClaudeCliChatModel(BaseChatModel):
    """A LangChain chat model that shells out to the Claude Code CLI
    (`claude -p --restricted`) instead of calling the Anthropic API - so RCA
    works off the same login as whatever `claude` session is on PATH, with no
    ANTHROPIC_API_KEY required. `--restricted` strips tool access (pure text
    generation), which is what makes this safe to invoke from a server
    request handler.
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
            [settings.rca_llm_command, "-p", "--restricted", prompt],
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
