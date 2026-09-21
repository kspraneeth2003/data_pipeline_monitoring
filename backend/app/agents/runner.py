"""Building and invoking an agent, with the guard rails both agents need.

`create_agent` is LangGraph's prebuilt loop: model -> tools -> model, until
the model stops asking for tools. What this module adds is everything that
makes it safe to run unattended on a timer.

**Budgets are not optional.** An agent that loops is a cost incident that
discovers itself on a bill. Every agent here is built with a hard ceiling on
model calls and tool calls, set in configuration, and `exit_behavior="end"`
so hitting the ceiling ends the run with whatever was decided so far instead
of raising. A partial answer from a capped agent is still useful; a crashed
sweep is not.

**Structured output, validated.** Both agents return decisions that get
written to the database, so the output is constrained to a pydantic schema
via `response_format` and re-validated on the way out. A model that returns
something unparseable is treated exactly like a model that is unreachable -
the caller falls back to rules - because a half-understood decision applied
to a ticket is worse than no decision.

**Nothing here writes.** Agents propose; the caller applies. That is what
keeps the blast radius of a bad model response bounded by the caller's own
validation rather than by the model's judgement.
"""

import logging
from typing import TypeVar

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ValidationError

from app.agents.model import require_model
from app.config import settings

logger = logging.getLogger("dpm.agents")

TResponse = TypeVar("TResponse", bound=BaseModel)


class AgentFailed(RuntimeError):
    """The agent did not produce a usable answer.

    One exception type for every failure mode - unreachable model, blown
    budget with nothing decided, unparseable output - because every caller
    responds to all of them the same way: use the rules instead.
    """


def build_agent(
    *,
    tools: list[BaseTool],
    system_prompt: str,
    response_format: type[BaseModel],
    name: str,
):
    return create_agent(
        model=require_model(),
        tools=tools,
        system_prompt=system_prompt,
        response_format=response_format,
        name=name,
        middleware=[
            # `end` rather than `error`: a capped run should return what it
            # has, not discard it.
            ModelCallLimitMiddleware(
                run_limit=settings.agent_max_model_calls, exit_behavior="end"
            ),
            ToolCallLimitMiddleware(
                run_limit=settings.agent_max_tool_calls, exit_behavior="end"
            ),
        ],
    )


def invoke_agent(
    agent,
    prompt: str,
    response_format: type[TResponse],
) -> TResponse:
    """Run an agent and return its validated structured response."""
    try:
        result = agent.invoke({"messages": [HumanMessage(content=prompt)]})
    except Exception as error:  # noqa: BLE001
        raise AgentFailed(f"Agent invocation failed: {error}") from error

    response = result.get("structured_response")
    if response is None:
        # Reached when the budget ran out before the model committed to an
        # answer. The transcript is logged rather than guessed at.
        raise AgentFailed(
            "Agent returned no structured response "
            f"(ran {len(result.get('messages', []))} messages; budget may have been exhausted)"
        )

    if isinstance(response, response_format):
        return response
    try:
        return response_format.model_validate(
            response if isinstance(response, dict) else response.model_dump()
        )
    except (ValidationError, AttributeError) as error:
        raise AgentFailed(f"Agent response did not validate: {error}") from error
