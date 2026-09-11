def synthesize_heuristic_rca(check_name: str, message: str, objects: list[dict]) -> dict:
    """Deterministic fallback used when the LLM call fails or is
    unavailable. Picks the most specific evidence available: a git commit
    that touched the offending column/keyword, the most recent commit to the
    mapped file, the most recent DDL statement, or an honest "needs manual
    investigation"."""
    for evidence in objects:
        git = evidence["git"]
        commit = git["targeted_commit"] or (git["recent_commits"][0] if git["recent_commits"] else None)
        if commit:
            specific = git["targeted_commit"] is not None
            short_hash = commit["hash"][:8]
            return {
                "summary": f"{check_name} failed: {message}",
                "rootCause": (
                    f'Commit {short_hash} ("{commit["subject"]}") by {commit["author_name"]} on '
                    f'{commit["date"]} changed the definition of {evidence["object"]} in '
                    f'{git["file_path"]}, and most likely caused this.'
                    if specific
                    else f'The most recent tracked change to {evidence["object"]} ({git["file_path"]}) is '
                    f'commit {short_hash} ("{commit["subject"]}") by {commit["author_name"]} on {commit["date"]}.'
                ),
                "confidence": 0.6 if specific else 0.4,
                "nextSteps": f"Review commit {short_hash} in {git['file_path']} and confirm it matches the deployed Snowflake object.",
                "suggestedOwner": commit["author_email"],
                "evidence": {"objects": objects, "source": "heuristic"},
            }

        ddl_events = evidence["snowflake"]["recent_ddl"]
        if ddl_events:
            ddl = ddl_events[0]
            return {
                "summary": f"{check_name} failed: {message}",
                "rootCause": (
                    f'A {ddl["query_type"]} statement ran against {evidence["object"]} on {ddl["start_time"]} '
                    f'by {ddl["user_name"]}, which may have caused this. No matching tracked file change was '
                    "found in git."
                ),
                "confidence": 0.3,
                "nextSteps": (
                    f'Check whether the {ddl["query_type"]} run by {ddl["user_name"]} on {ddl["start_time"]} '
                    f"should be reflected in the tracked SQL for {evidence['object']}."
                ),
                "suggestedOwner": None,
                "evidence": {"objects": objects, "source": "heuristic"},
            }

    return {
        "summary": f"{check_name} failed: {message}",
        "rootCause": (
            "No recent git history or Snowflake DDL activity was found for the object(s) involved. This may "
            "be a data-quality issue upstream rather than a schema/pipeline change."
        ),
        "confidence": 0.1,
        "nextSteps": "Manually inspect the source data and recent pipeline runs for this object.",
        "suggestedOwner": None,
        "evidence": {"objects": objects, "source": "heuristic"},
    }
