"""The repo-ingestion agent: a repository URL in, a reviewable project out.

Built as a LangGraph `StateGraph` for the same reason RCA is - the interesting
property is not the graph, it is the edge that makes the LLM optional:

    fetch -> parse -> heuristic -> llm_enrich -> done
                          |            |
                          +-- (on any LLM failure) -> done

`heuristic` runs first and always, so the LLM is only ever *improving* a result
that already exists. If the CLI is missing, slow, or returns something that
does not validate, ingestion still produces the full deterministic proposal set
and says so in `llm_error`. There is no path where a broken LLM means a broken
setup flow.

The LLM's output is never trusted structurally: every check it proposes is
validated against the same pydantic config schema the API enforces, and
anything that fails is dropped with a warning rather than handed to the user as
a check that will error on first run.
"""

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import END, StateGraph

from app.checks.config_schemas import CONFIG_SCHEMAS_BY_TYPE
from app.config import settings
from app.ingest.ddl_parser import ParsedRepo, parse_repo
from app.ingest.heuristic import CheckProposal, propose_checks, propose_databases
from app.ingest.repo import RepoCheckout, fetch_repo
from app.rca.llm import ClaudeCliChatModel, extract_json

logger = logging.getLogger(__name__)

# How much of the repo's DDL to put in front of the model. Large enough for a
# realistic bronze/silver/gold pipeline, small enough that a monorepo cannot
# blow up the prompt.
MAX_PROMPT_TABLES = 60
MAX_PROMPT_MERGES = 30

VALID_SCHEDULE_TYPES = set(CONFIG_SCHEMAS_BY_TYPE)


class IngestState(TypedDict):
    # Reports which node is running, so a 90-second analysis can say what it is
    # doing instead of showing an unmoving spinner.
    on_stage: Callable[[str], None]

    repo_url: str
    token: str | None
    ref: str | None

    checkout: RepoCheckout | None
    parsed: ParsedRepo | None

    project_name: str
    project_description: str
    databases: list[dict]
    proposals: list[CheckProposal]

    llm_error: str | None
    warnings: list[str]


def _fetch(state: IngestState) -> IngestState:
    state["on_stage"]("fetch")
    checkout = fetch_repo(state["repo_url"], state["token"], state["ref"])
    return {**state, "checkout": checkout}


def _parse(state: IngestState) -> IngestState:
    state["on_stage"]("parse")
    checkout = state["checkout"]
    assert checkout is not None
    parsed = parse_repo(Path(checkout["path"]))
    return {**state, "parsed": parsed, "warnings": [*state["warnings"], *parsed["warnings"]]}


def _fallback_name(repo_url: str) -> str:
    slug = repo_url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    return slug.replace("-", " ").replace("_", " ").title() or "Imported project"


def _heuristic(state: IngestState) -> IngestState:
    state["on_stage"]("heuristic")
    parsed = state["parsed"]
    assert parsed is not None
    databases = propose_databases(parsed)
    proposals = propose_checks(parsed)
    return {
        **state,
        "databases": databases,
        "proposals": proposals,
        "project_name": _fallback_name(state["repo_url"]),
        "project_description": (
            f"Imported from {state['repo_url']}. "
            f"{len(databases)} database(s), {len(parsed['tables'])} table(s)."
        ),
    }


def _build_prompt(state: IngestState) -> str:
    parsed = state["parsed"]
    assert parsed is not None

    structure = {
        "databases": [
            {"name": d["name"], "schemas": sorted(d["repo_paths"]), "tables": d["tables"]}
            for d in state["databases"]
        ],
        "tables": [
            {
                "object": t["fqn"],
                "comment": t["comment"],
                "columns": [f"{c['name']} {c['data_type']}" for c in t["columns"]],
            }
            for t in parsed["tables"][:MAX_PROMPT_TABLES]
        ],
        "pipelines": [
            {
                "from": m["source"],
                "to": m["target"],
                "keys": [k["name"] for k in m["key_columns"]],
                "filtered": m["filtered"],
            }
            for m in parsed["merges"][:MAX_PROMPT_MERGES]
        ],
    }

    existing = [
        {"key": p["key"], "type": p["type"], "name": p["name"], "config": p["config"]}
        for p in state["proposals"]
    ]

    return f"""You are setting up data-integrity monitoring for a Snowflake pipeline that is defined as SQL DDL in a git repository.

Here is what was parsed out of the repository:

{json.dumps(structure, indent=2, default=str)}

A deterministic pass has already proposed these checks, derived directly from the MERGE statements, task schedules and CREATE TABLE contracts:

{json.dumps(existing, indent=2, default=str)}

Your job is to name the data product and add the checks the deterministic pass could not derive - the ones that need judgement about what the data *means*, not just what the DDL says. Good candidates: NULL_RATE on a column that is business-critical (an email, an amount, a foreign key), and FRESHNESS or ROW_COUNT on something the rules missed.

Do NOT restate or duplicate the checks listed above.

Available check types and their exact config shapes:
- NULL_RATE: {{"object": "DB.SCHEMA.TABLE", "column": "COL", "maxNullRatio": 0.0-1.0}}
- FRESHNESS: {{"object": "DB.SCHEMA.TABLE", "timestampColumn": "COL", "maxAgeMinutes": number}}
- ROW_COUNT: {{"object": "DB.SCHEMA.TABLE", "comparisonObject": "DB.SCHEMA.TABLE" or null, "minRows": number or null, "toleranceAbs": number}}

Respond with ONLY valid JSON (no markdown fences, no commentary) matching exactly this shape:
{{"projectName": string, "projectDescription": string, "checks": [{{"name": string, "description": string, "rationale": string, "type": string, "database": string, "config": object}}]}}

- "projectName" is a short business name for this data product (e.g. "Customer 360"), not the repository slug.
- "projectDescription" is one sentence on what the data product is for.
- "database" must be one of the database names listed above, and every "object" must be a fully-qualified DB.SCHEMA.TABLE that appears above. Do not invent tables or columns.
- "rationale" says, in one sentence, why this check is worth running - cite the column or table that makes it matter.
- Propose at most 6 checks. Fewer good ones is better than padding."""


def _validate_llm_check(raw: dict, known_databases: set[str]) -> tuple[CheckProposal | None, str | None]:
    """Structural validation against the same schemas the API enforces.

    A check that does not validate here would be accepted into the database and
    then error on its first run, which looks like the pipeline is broken rather
    than the proposal. Dropping it with a warning is the honest outcome.
    """
    check_type = str(raw.get("type", "")).upper()
    schema = CONFIG_SCHEMAS_BY_TYPE.get(check_type)
    if not schema:
        return None, f"dropped a proposed check with unknown type {check_type!r}"

    database = str(raw.get("database", "")).upper()
    if database not in known_databases:
        return None, f"dropped {raw.get('name') or check_type!r}: database {database!r} is not in this repository"

    config = raw.get("config")
    if not isinstance(config, dict):
        return None, f"dropped {raw.get('name') or check_type!r}: config was not an object"

    try:
        validated = schema.model_validate(config).model_dump()
    except Exception as error:  # noqa: BLE001
        reason = str(error).splitlines()[0][:160]
        return None, f"dropped {raw.get('name') or check_type!r}: {reason}"

    name = str(raw.get("name") or f"{check_type} check").strip()
    return {
        "key": f"llm:{check_type}:{name}",
        "name": name,
        "description": str(raw.get("description") or "").strip(),
        "rationale": str(raw.get("rationale") or "Proposed by the ingestion agent.").strip(),
        "type": check_type,
        "schedule": "*/15 * * * *",
        "database": database,
        "config": validated,
        "source": "llm",
    }, None


def _llm_enrich(state: IngestState) -> IngestState:
    state["on_stage"]("llm_enrich")
    if not state["databases"]:
        # Nothing was parsed, so there is nothing for the model to reason about.
        return {**state, "llm_error": None}

    try:
        model = ClaudeCliChatModel(timeout_seconds=settings.ingest_llm_timeout_seconds)
        response = model.invoke([HumanMessage(content=_build_prompt(state))])
        parsed = extract_json(str(response.content))
    except Exception as error:  # noqa: BLE001
        logger.warning("Repo ingestion LLM step failed: %s", error)
        return {**state, "llm_error": str(error)[:300]}

    warnings = list(state["warnings"])
    known = {d["name"].upper() for d in state["databases"]}
    existing_objects = {
        (p["type"], json.dumps(p["config"], sort_keys=True)) for p in state["proposals"]
    }

    additions: list[CheckProposal] = []
    for raw in parsed.get("checks") or []:
        if not isinstance(raw, dict):
            continue
        proposal, warning = _validate_llm_check(raw, known)
        if warning:
            warnings.append(f"Ingestion agent: {warning}.")
        if not proposal:
            continue
        signature = (proposal["type"], json.dumps(proposal["config"], sort_keys=True))
        if signature in existing_objects:
            continue
        existing_objects.add(signature)
        additions.append(proposal)

    name = str(parsed.get("projectName") or "").strip()
    description = str(parsed.get("projectDescription") or "").strip()

    return {
        **state,
        "project_name": name or state["project_name"],
        "project_description": description or state["project_description"],
        "proposals": [*state["proposals"], *additions],
        "warnings": warnings,
        "llm_error": None,
    }


_graph = StateGraph(IngestState)
_graph.add_node("fetch", _fetch)
_graph.add_node("parse", _parse)
_graph.add_node("heuristic", _heuristic)
_graph.add_node("llm_enrich", _llm_enrich)

_graph.set_entry_point("fetch")
_graph.add_edge("fetch", "parse")
_graph.add_edge("parse", "heuristic")
_graph.add_edge("heuristic", "llm_enrich")
_graph.add_edge("llm_enrich", END)

ingest_graph = _graph.compile()


def analyze_repository(
    repo_url: str,
    token: str | None = None,
    ref: str | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> dict:
    """Runs the ingestion agent and returns a reviewable analysis.

    Raises `RepoError` if the repository cannot be fetched at all - that is the
    one failure the user has to fix before anything else can happen. Every other
    failure degrades into a warning.
    """
    initial: IngestState = {
        "on_stage": on_stage or (lambda _stage: None),
        "repo_url": repo_url,
        "token": token,
        "ref": ref,
        "checkout": None,
        "parsed": None,
        "project_name": "",
        "project_description": "",
        "databases": [],
        "proposals": [],
        "llm_error": None,
        "warnings": [],
    }
    final = ingest_graph.invoke(initial)

    checkout = final["checkout"]
    parsed = final["parsed"]
    warnings = list(final["warnings"])
    if final["llm_error"]:
        warnings.append(
            "The ingestion agent could not refine these proposals, so they are the "
            f"rule-derived set only ({final['llm_error']})."
        )

    return {
        "repo_url": checkout["url"] if checkout else repo_url,
        "repo_ref": checkout["ref"] if checkout else None,
        "repo_commit": checkout["commit"] if checkout else None,
        "repo_commit_subject": checkout["subject"] if checkout else None,
        "repo_path": checkout["path"] if checkout else None,
        "project_name": final["project_name"],
        "project_description": final["project_description"],
        "databases": final["databases"],
        "checks": final["proposals"],
        "sql_files": parsed["files"] if parsed else [],
        "table_count": len(parsed["tables"]) if parsed else 0,
        "llm_error": final["llm_error"],
        "warnings": warnings,
    }
