"""The tool-calling protocol over the Claude Code CLI.

The CLI itself is never invoked here - `_invoke_cli` is replaced with a
canned reply. What is under test is the translation layer, which is the part
that has to be right: a text protocol has failure modes a native
tool-calling API simply does not have, and each one below is a way the loop
could break or, worse, quietly do the wrong thing.

The reliability of the *model* following the protocol is a separate
question, measured by running it, not by asserting on it here.
"""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from app.agents.claude_cli import (
    ClaudeCliChatModel,
    extract_json_object,
    render_conversation,
)


@tool
def get_run_history(check_name: str) -> str:
    """Recent runs of a check."""
    return "{}"


@tool
def get_incident(incident_id: str) -> str:
    """One incident."""
    return "{}"


def model_returning(*replies: str) -> ClaudeCliChatModel:
    """A model whose CLI returns these replies in order."""
    model = ClaudeCliChatModel()
    queue = list(replies)
    object.__setattr__(model, "_queue", queue)
    model._invoke_cli = lambda system, prompt: queue.pop(0)  # type: ignore[method-assign]
    return model


def bound(model: ClaudeCliChatModel):
    return model.bind_tools([get_run_history, get_incident])


class TestReplyExtraction:
    def test_bare_json(self):
        assert extract_json_object('{"final": "hi"}') == {"final": "hi"}

    def test_fenced_json(self):
        assert extract_json_object('```json\n{"final": "hi"}\n```') == {"final": "hi"}

    def test_json_with_prose_around_it(self):
        # The protocol forbids this, but models do it anyway, and throwing
        # away a correct decision over a stray sentence would be silly.
        text = 'Sure, here you go:\n{"final": "hi"}\nHope that helps.'
        assert extract_json_object(text) == {"final": "hi"}

    def test_nested_objects_are_matched_by_braces_not_regex(self):
        # A greedy or non-greedy regex both get this wrong; brace matching
        # is why the parser is written the way it is.
        text = 'text {"tool_calls": [{"name": "a", "arguments": {"b": {"c": 1}}}]} tail'
        assert extract_json_object(text)["tool_calls"][0]["arguments"] == {"b": {"c": 1}}

    def test_braces_inside_strings_do_not_confuse_it(self):
        text = '{"final": "a } brace in a string"}'
        assert extract_json_object(text) == {"final": "a } brace in a string"}

    def test_unparseable_returns_none(self):
        assert extract_json_object("no json here at all") is None
        assert extract_json_object("") is None

    def test_a_json_array_is_not_a_reply(self):
        assert extract_json_object('[{"final": "hi"}]') is None


class TestToolCalls:
    def test_a_tool_call_becomes_a_real_tool_call(self):
        reply = json.dumps(
            {
                "reasoning": "need history",
                "tool_calls": [
                    {"name": "get_run_history", "arguments": {"check_name": "X"}}
                ],
            }
        )
        message = bound(model_returning(reply)).invoke([HumanMessage(content="go")])
        assert len(message.tool_calls) == 1
        call = message.tool_calls[0]
        assert call["name"] == "get_run_history"
        assert call["args"] == {"check_name": "X"}
        # LangGraph matches tool results back to calls by id, so every call
        # needs a distinct one - the CLI does not supply them.
        assert call["id"]
        assert message.content == "need history"

    def test_parallel_tool_calls_get_distinct_ids(self):
        reply = json.dumps(
            {
                "tool_calls": [
                    {"name": "get_run_history", "arguments": {"check_name": "X"}},
                    {"name": "get_incident", "arguments": {"incident_id": "i1"}},
                ]
            }
        )
        message = bound(model_returning(reply)).invoke([HumanMessage(content="go")])
        ids = [c["id"] for c in message.tool_calls]
        assert len(ids) == 2
        assert len(set(ids)) == 2

    def test_a_final_answer_has_no_tool_calls(self):
        reply = json.dumps({"reasoning": "done", "final": "all clear"})
        message = bound(model_returning(reply)).invoke([HumanMessage(content="go")])
        assert message.tool_calls == []
        assert message.content == "all clear"

    def test_a_non_string_final_is_serialised(self):
        reply = json.dumps({"final": {"action": "NONE"}})
        message = bound(model_returning(reply)).invoke([HumanMessage(content="go")])
        assert json.loads(message.content) == {"action": "NONE"}

    def test_missing_arguments_become_an_empty_dict(self):
        # Better than passing None into a tool signature and raising inside
        # LangGraph, where the model gets no chance to correct itself.
        reply = json.dumps({"tool_calls": [{"name": "get_run_history"}]})
        message = bound(model_returning(reply)).invoke([HumanMessage(content="go")])
        assert message.tool_calls[0]["args"] == {}


class TestProtocolFailures:
    def test_an_unparseable_reply_is_retried_once(self):
        good = json.dumps({"final": "recovered"})
        model = model_returning("not json", good)
        message = bound(model).invoke([HumanMessage(content="go")])
        assert message.content == "recovered"

    def test_a_hallucinated_tool_name_is_retried_not_passed_on(self):
        # Passing an unknown name through would make LangGraph raise and end
        # the run; retrying gives the model a chance to correct itself.
        bad = json.dumps({"tool_calls": [{"name": "drop_everything", "arguments": {}}]})
        good = json.dumps({"final": "ok"})
        message = bound(model_returning(bad, good)).invoke([HumanMessage(content="go")])
        assert message.content == "ok"

    def test_giving_up_returns_text_rather_than_raising(self):
        # The agent then finishes with no structured response, which
        # `invoke_agent` rejects and the caller handles by using the rules.
        # Raising here would instead surface as an unhandled sweep failure.
        model = model_returning("garbage", "still garbage")
        message = bound(model).invoke([HumanMessage(content="go")])
        assert message.tool_calls == []
        assert "garbage" in message.content

    def test_a_reply_with_neither_shape_is_treated_as_malformed(self):
        odd = json.dumps({"reasoning": "hmm"})
        good = json.dumps({"final": "ok"})
        message = bound(model_returning(odd, good)).invoke([HumanMessage(content="go")])
        assert message.content == "ok"


class TestConversationRendering:
    def test_system_messages_are_separated_from_the_transcript(self):
        # They go to `--system-prompt`, not into the user turn.
        system, transcript = render_conversation(
            [SystemMessage(content="be terse"), HumanMessage(content="hello")]
        )
        assert system == "be terse"
        assert "hello" in transcript
        assert "be terse" not in transcript

    def test_tool_results_are_attributed_to_their_tool(self):
        # Several parallel results arrive as an unlabelled pile otherwise,
        # and get attributed to the wrong call.
        _, transcript = render_conversation(
            [
                HumanMessage(content="go"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "get_run_history", "args": {}, "id": "1", "type": "tool_call"}
                    ],
                ),
                ToolMessage(content="some rows", tool_call_id="1", name="get_run_history"),
            ]
        )
        assert "[result of get_run_history]" in transcript
        assert "some rows" in transcript

    def test_prior_tool_calls_are_replayed(self):
        # Without this the model cannot see what it already asked for and
        # asks again, burning the loop budget on repeats.
        _, transcript = render_conversation(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "get_incident",
                            "args": {"incident_id": "i1"},
                            "id": "1",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        )
        assert "get_incident" in transcript
        assert "i1" in transcript


class TestWithoutTools:
    def test_no_tools_means_no_protocol(self):
        # An unbound model is a plain text generator, as RCA uses it. Asking
        # for JSON there would only wrap a plain answer in ceremony.
        model = model_returning("just some prose")
        message = model.invoke([HumanMessage(content="hi")])
        assert message.content == "just some prose"
        assert message.tool_calls == []


class TestCliInvocation:
    def test_the_cli_is_given_no_tools_of_its_own(self, monkeypatch):
        """The CLI must be a pure generator.

        The tools in play belong to this application and are executed by
        LangGraph. A CLI that could also read files or run commands would be
        a second, ungoverned agent running inside the first one.
        """
        captured = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = argv

            class Result:
                returncode = 0
                stdout = json.dumps({"result": '{"final": "ok"}', "is_error": False})
                stderr = ""

            return Result()

        monkeypatch.setattr("app.agents.claude_cli.subprocess.run", fake_run)
        monkeypatch.setattr("app.agents.claude_cli.resolve_cli", lambda c: c)
        bound(ClaudeCliChatModel()).invoke([HumanMessage(content="go")])

        argv = captured["argv"]
        assert "--allowedTools" in argv
        assert argv[argv.index("--allowedTools") + 1] == ""
        assert "-p" in argv and "--output-format" in argv

    def test_a_cli_error_envelope_raises(self, monkeypatch):
        def fake_run(argv, **kwargs):
            class Result:
                returncode = 0
                stdout = json.dumps({"result": "rate limited", "is_error": True})
                stderr = ""

            return Result()

        monkeypatch.setattr("app.agents.claude_cli.subprocess.run", fake_run)
        monkeypatch.setattr("app.agents.claude_cli.resolve_cli", lambda c: c)
        with pytest.raises(RuntimeError, match="reported an error"):
            ClaudeCliChatModel().invoke([HumanMessage(content="go")])
