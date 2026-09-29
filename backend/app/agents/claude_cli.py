"""A LangChain chat model backed by the Claude Code CLI, with tool calling.

This is what lets both agents run with no API key at all: they drive the
`claude` binary that is already on the machine, using whatever session it is
logged in with. `AGENT_MODEL=claude-cli` selects it.

**The problem this solves.** An agent loop needs a model that emits
structured tool calls - `create_agent` reads `AIMessage.tool_calls` to decide
what to run next, and (because it defaults to `ToolStrategy`) it also
delivers the final structured response as a tool call. `claude -p` returns
text. So the CLI cannot drive an agent loop as-is, which is why the RCA
model in `rca/llm.py` passes `--allowedTools ""` and is a pure text
generator.

The fix is a text protocol: the tool catalogue is rendered into the prompt,
the model is asked to reply with one JSON object naming either a tool call
or a final answer, and the reply is parsed back into a real `AIMessage` with
`tool_calls` populated. LangGraph is none the wiser - it sees the same
message shape it would get from a provider with native tool calling, so
`create_agent`, the middleware and the structured-output strategy all work
unchanged.

**What this costs, and why it is worth knowing before turning it on.**

* *Latency.* Every turn is a fresh process, and a measured turn here took
  ~19s wall (6s of it model time). An agent that makes eight tool calls
  therefore takes two to three minutes. Budgets default lower on this path
  (`AGENT_MAX_MODEL_CALLS_CLI`) for that reason.
* *No prompt caching.* Each turn re-sends the whole conversation as a new
  prompt, so cost grows quadratically with loop length rather than linearly.
  `claude --resume` would avoid this by keeping a server-side session; it is
  deliberately not used yet, because it would mean the CLI's history and
  LangGraph's message list could drift apart, and a silent divergence is far
  harder to debug than a slow loop.
* *Reliability.* A native tool-calling API guarantees a parseable call. A
  text protocol does not. Every failure mode below is handled explicitly,
  and the agent still falls back to the rules if this model cannot produce
  something usable.

So: correct and free, but slow. For a monitor that sweeps every five
minutes that is a good trade; for anything interactive it would not be.
"""

import json
import logging
import re
import shutil
import subprocess
import uuid
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool

logger = logging.getLogger("dpm.agents.claude_cli")

# How the model is told to answer. Deliberately one object with two mutually
# exclusive shapes: asking for "either a tool call or an answer" in prose
# produces both, or neither, often enough to matter.
PROTOCOL_HEADER = """\
# How to reply

You are driving a program, not talking to a person. Every reply must be a \
single JSON object and nothing else - no prose before it, no code fence \
around it, no commentary after it.
"""

PROTOCOL_TOOL_CALL = """\
To call tools:

    {"reasoning": "<one short sentence on why>",
     "tool_calls": [{"name": "<tool name>", "arguments": {<arguments>}}]}
"""

PROTOCOL_FREE_FINISH = """\
To give your final answer:

    {"reasoning": "<one short sentence>", "final": <your answer>}
"""

# Used when the caller has a required response tool. Without this the model
# reaches for the `final` shape to answer in prose, the agent ends with no
# structured response, and the whole run is discarded - measured at two
# failures in three before this branch existed.
PROTOCOL_REQUIRED_FINISH = """\
When you are ready to answer, you do NOT use a "final" field. There is no \
"final" field. You finish by calling the tool named `{tool}`, which is how \
your answer is recorded:

    {{"reasoning": "<one short sentence>",
      "tool_calls": [{{"name": "{tool}", "arguments": {{<your answer>}}}}]}}

Every run must end with a call to `{tool}`. Answering in prose, or with a \
"final" field, discards your work entirely.
"""

PROTOCOL_RULES = """\
Rules:

- Call a tool only if it appears in the catalogue above. Use its exact name.
- `arguments` must match that tool's schema. Omit optional arguments you do \
not need; never invent argument names.
- You may request several tool calls at once when they are independent. \
Prefer that over one at a time - each round trip is expensive.
- Do not keep calling tools to be thorough. Gather what you need, then \
finish.
- If a tool returns an error, do not call it again with identical \
arguments. Either fix the arguments or proceed without it.
"""


def build_protocol(response_tool_name: str | None) -> str:
    parts = [PROTOCOL_HEADER, PROTOCOL_TOOL_CALL]
    if response_tool_name:
        parts.append(PROTOCOL_REQUIRED_FINISH.format(tool=response_tool_name))
    else:
        parts.append(PROTOCOL_FREE_FINISH)
    parts.append(PROTOCOL_RULES)
    return "\n".join(parts)


def resolve_cli(command: str) -> str:
    """Resolve the CLI to a real executable before exec'ing it.

    On Windows the install directory holds both an extensionless shim and
    `claude.exe`, and `CreateProcess` picks the shim and fails with "Access
    is denied" - so passing the bare name raises WinError 5 while the same
    command works from a shell. `shutil.which` applies PATHEXT and lands on
    something executable.
    """
    return shutil.which(command) or command


def extract_json_object(text: str) -> dict | None:
    """Pull the reply object out of whatever the model actually sent.

    Three fallbacks, in order of how often each is needed: the text is
    already JSON; it is JSON inside a code fence; or there is prose around a
    JSON object. The last is brace-matched rather than regex-matched,
    because the objects here nest and a greedy pattern captures the wrong
    span.
    """
    text = text.strip()
    if not text:
        return None

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1).strip())
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start : index + 1])
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def render_tool_catalogue(tools: list[dict]) -> str:
    lines = ["# Tools you can call", ""]
    for tool in tools:
        function = tool.get("function", tool)
        name = function.get("name", "?")
        description = (function.get("description") or "").strip()
        schema = function.get("parameters") or {}
        lines.append(f"## {name}")
        if description:
            lines.append(description)
        lines.append(f"arguments schema: {json.dumps(schema)}")
        lines.append("")
    return "\n".join(lines)


def render_conversation(messages: list[BaseMessage]) -> tuple[str, str]:
    """Split the message list into a system prompt and a transcript.

    Tool results are rendered as observations attributed to the call that
    produced them, so a model looking at three parallel results can tell
    which is which - without that they arrive as an unlabelled pile and get
    attributed to the wrong call.
    """
    system_parts: list[str] = []
    transcript: list[str] = []

    for message in messages:
        if isinstance(message, SystemMessage):
            system_parts.append(str(message.content))
        elif isinstance(message, HumanMessage):
            transcript.append(f"[user]\n{message.content}")
        elif isinstance(message, AIMessage):
            if message.tool_calls:
                calls = json.dumps(
                    [
                        {"name": c["name"], "arguments": c["args"]}
                        for c in message.tool_calls
                    ],
                    default=str,
                )
                transcript.append(f"[you called tools]\n{calls}")
            if message.content:
                transcript.append(f"[you]\n{message.content}")
        elif isinstance(message, ToolMessage):
            name = getattr(message, "name", None) or "tool"
            transcript.append(f"[result of {name}]\n{message.content}")

    return "\n\n".join(system_parts), "\n\n".join(transcript)


class ClaudeCliChatModel(BaseChatModel):
    """Chat model that shells out to the Claude Code CLI.

    Supports tool calling through the text protocol above, so it can drive
    `create_agent` the way a native tool-calling model does.
    """

    timeout_seconds: int = 300
    command: str = "claude"
    # Passed to `--model` when set, so the CLI's own default can be
    # overridden without touching this code.
    cli_model: str | None = None
    # One retry when the model returns something unparseable. Worth exactly
    # one: the second failure is nearly always the same failure, and each
    # attempt costs another ~19 seconds.
    parse_retries: int = 1
    # The tool that records the agent's answer, when the caller requires a
    # structured response. Set by `build_agent`, because only the caller
    # knows which of the bound tools is the response schema - and without
    # it the model answers in prose and the run is thrown away.
    response_tool_name: str | None = None

    @property
    def _llm_type(self) -> str:
        return "claude-cli"

    def bind_tools(self, tools, *, tool_choice: Any = None, **kwargs: Any):
        """Accept tools the way a native tool-calling model does.

        Converted to the OpenAI function shape because it is the one every
        LangChain tool type converts into cleanly, and the rendering above
        only needs name/description/schema.
        """
        formatted = [
            convert_to_openai_tool(tool)
            for tool in tools
            if isinstance(tool, (BaseTool, dict, type)) or callable(tool)
        ]
        if tool_choice is not None:
            # There is no way to force a call through a text protocol; the
            # instruction below is the closest honest equivalent.
            kwargs["tool_choice"] = tool_choice
        return self.bind(tools=formatted, **kwargs)

    def _invoke_cli(self, system_prompt: str, prompt: str) -> str:
        argv = [resolve_cli(self.command), "-p", "--output-format", "json"]
        # No tools of its own. The CLI must be a pure generator here: the
        # tools in play are this application's, executed by LangGraph, and a
        # CLI that could also read files or run commands would be a second,
        # ungoverned agent inside the first.
        argv += ["--allowedTools", ""]
        if system_prompt:
            argv += ["--system-prompt", system_prompt]
        if self.cli_model:
            argv += ["--model", self.cli_model]

        result = subprocess.run(
            argv,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"claude CLI exited {result.returncode}: {result.stderr.strip()[:400]}"
            )

        try:
            envelope = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"claude CLI returned non-JSON output: {result.stdout[:300]}"
            ) from error

        if envelope.get("is_error"):
            raise RuntimeError(
                f"claude CLI reported an error: {str(envelope.get('result'))[:300]}"
            )
        return str(envelope.get("result") or "")

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        tools: list[dict] = kwargs.get("tools") or []
        system_prompt, transcript = render_conversation(messages)

        sections = [transcript]
        if tools:
            sections.append(render_tool_catalogue(tools))
            # Only require the response tool if it is actually on the table
            # this turn; requiring one the model cannot see would strand it.
            names = {(t.get("function", t)).get("name") for t in tools}
            required = (
                self.response_tool_name if self.response_tool_name in names else None
            )
            sections.append(build_protocol(required))
        else:
            # Without tools there is nothing to call, so the protocol would
            # only invite the model to wrap a plain answer in JSON.
            sections.append("Reply with your answer and nothing else.")
        prompt = "\n\n".join(part for part in sections if part)

        raw = self._invoke_cli(system_prompt, prompt)
        if not tools:
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content=raw.strip()))]
            )

        for attempt in range(self.parse_retries + 1):
            reply = extract_json_object(raw)
            if reply is not None:
                message = self._to_message(reply, tools)
                if message is not None:
                    return ChatResult(generations=[ChatGeneration(message=message)])

            if attempt >= self.parse_retries:
                break
            logger.warning(
                "Claude CLI reply did not match the protocol; retrying once. Got: %s",
                raw[:200],
            )
            raw = self._invoke_cli(
                system_prompt,
                prompt
                + "\n\n[system]\nYour previous reply was not a single valid JSON "
                "object of the required shape. Send only the JSON object, with no "
                "surrounding text.",
            )

        # Out of retries. The text is returned as a plain answer rather than
        # raised: the agent then finishes with something a human can read,
        # and `invoke_agent` rejects it for lack of a structured response -
        # which the caller already handles by falling back to the rules.
        logger.warning("Claude CLI never produced a usable reply; returning raw text")
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=raw.strip()))]
        )

    def _to_message(self, reply: dict, tools: list[dict]) -> AIMessage | None:
        """Turn a parsed reply into an AIMessage, or None if it is malformed."""
        reasoning = str(reply.get("reasoning") or "")

        if "tool_calls" in reply and reply["tool_calls"]:
            known = {
                (t.get("function", t)).get("name") for t in tools
            }
            calls = []
            for raw_call in reply["tool_calls"]:
                if not isinstance(raw_call, dict):
                    continue
                name = raw_call.get("name")
                if name not in known:
                    # A hallucinated tool name is treated as a protocol
                    # failure rather than passed on: LangGraph would raise
                    # on an unknown tool, ending the run instead of letting
                    # the model correct itself on the retry.
                    logger.warning("Claude CLI asked for unknown tool %r", name)
                    return None
                arguments = raw_call.get("arguments")
                if not isinstance(arguments, dict):
                    arguments = {}
                calls.append(
                    {
                        "name": name,
                        "args": arguments,
                        "id": f"call_{uuid.uuid4().hex[:16]}",
                        "type": "tool_call",
                    }
                )
            if not calls:
                return None
            return AIMessage(content=reasoning, tool_calls=calls)

        if "final" in reply:
            final = reply["final"]
            content = final if isinstance(final, str) else json.dumps(final, default=str)
            return AIMessage(content=content)

        return None
