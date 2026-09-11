from typing import TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import END, StateGraph

from app.connectors.registry import build_connector
from app.rca.extract_targets import extract_rca_targets
from app.rca.git_context import gather_git_context
from app.rca.heuristic import synthesize_heuristic_rca
from app.rca.llm import ClaudeCliChatModel, extract_json
from app.rca.snowflake_context import gather_snowflake_context


class RcaState(TypedDict):
    check_name: str
    check_type: str
    config: dict
    connector_type: str
    connector_config: dict
    message: str
    metrics: dict
    objects: list[dict]
    llm_error: str | None
    result: dict | None


def _gather_evidence(state: RcaState) -> RcaState:
    from app.checks.engine import CheckOutcome

    outcome = CheckOutcome(status="FAILED", metrics=state["metrics"], message=state["message"])
    targets = extract_rca_targets(state["check_type"], state["config"], outcome)

    connector = build_connector(state["connector_type"], state["connector_config"])
    objects: list[dict] = []
    try:
        for target in targets:
            snowflake_ctx = gather_snowflake_context(connector, target["object"])
            git_ctx = gather_git_context(target["object"], target["keyword"])
            objects.append({"object": target["object"], "snowflake": snowflake_ctx, "git": git_ctx})
    finally:
        connector.close()

    return {**state, "objects": objects}


def _build_prompt(check_name: str, message: str, objects: list[dict]) -> str:
    import json

    evidence_block = "\n\n".join(
        json.dumps(
            {
                "object": o["object"],
                "snowflake": o["snowflake"],
                "git": o["git"] if o["git"]["file_path"] else "not tracked in git",
            },
            indent=2,
            default=str,
        )
        for o in objects
    )

    return f"""You are a root-cause-analysis assistant for a data integrity monitoring system.

A data check named "{check_name}" just failed with this message:
"{message}"

Here is the evidence gathered automatically (Snowflake object metadata/DDL history, and git history of the tracked SQL defining these objects):

{evidence_block or "No object-level evidence could be gathered for this check type."}

Based ONLY on this evidence, respond with ONLY valid JSON (no markdown fences, no commentary) matching exactly this shape:
{{"summary": string, "rootCause": string, "confidence": number between 0 and 1, "nextSteps": string, "suggestedOwner": string or null}}

- "summary" is one sentence stating what failed.
- "rootCause" explains the most likely cause, citing specific evidence (commit hash/author/date, or DDL statement) when available. If evidence is thin, say so honestly rather than speculating with false confidence.
- "confidence" should be low (< 0.3) if the evidence doesn't clearly point to a cause.
- "suggestedOwner" is the git author email most likely responsible, or null if none is evident."""


def _synthesize_llm(state: RcaState) -> RcaState:
    prompt = _build_prompt(state["check_name"], state["message"], state["objects"])
    try:
        model = ClaudeCliChatModel()
        response = model.invoke([HumanMessage(content=prompt)])
        parsed = extract_json(str(response.content))
        result = {
            "summary": parsed["summary"],
            "rootCause": parsed["rootCause"],
            "confidence": float(parsed["confidence"]),
            "nextSteps": parsed["nextSteps"],
            "suggestedOwner": parsed.get("suggestedOwner"),
            "evidence": {"objects": state["objects"], "source": "llm"},
        }
        return {**state, "result": result, "llm_error": None}
    except Exception as error:  # noqa: BLE001
        return {**state, "result": None, "llm_error": str(error)}


def _synthesize_heuristic(state: RcaState) -> RcaState:
    result = synthesize_heuristic_rca(state["check_name"], state["message"], state["objects"])
    return {**state, "result": result}


def _route_after_llm(state: RcaState) -> str:
    return "done" if state.get("result") else "heuristic"


_graph = StateGraph(RcaState)
_graph.add_node("gather_evidence", _gather_evidence)
_graph.add_node("synthesize_llm", _synthesize_llm)
_graph.add_node("synthesize_heuristic", _synthesize_heuristic)

_graph.set_entry_point("gather_evidence")
_graph.add_edge("gather_evidence", "synthesize_llm")
_graph.add_conditional_edges("synthesize_llm", _route_after_llm, {"done": END, "heuristic": "synthesize_heuristic"})
_graph.add_edge("synthesize_heuristic", END)

rca_graph = _graph.compile()


def generate_rca(
    check_name: str,
    check_type: str,
    config: dict,
    connector_type: str,
    connector_config: dict,
    message: str,
    metrics: dict,
) -> dict:
    initial_state: RcaState = {
        "check_name": check_name,
        "check_type": check_type,
        "config": config,
        "connector_type": connector_type,
        "connector_config": connector_config,
        "message": message,
        "metrics": metrics,
        "objects": [],
        "llm_error": None,
        "result": None,
    }
    final_state = rca_graph.invoke(initial_state)
    return final_state["result"]
