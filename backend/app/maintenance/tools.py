"""What the maintenance agent can look at.

Read-only, and split between the app's database (what the checks currently
say) and the repository checkout (what the DDL now says). The agent needs
both because its whole job is reconciling one against the other.

`read_repo_file` and `grep_repo` are the tools that let this agent do
something the parser cannot. The parser is lexical and gives up on shapes it
does not recognise - dbt models, Jinja, view-only definitions - but a model
reading the file directly can still say what a change means. That is the
main reason the repo is exposed as files rather than only as parsed output.

Both are jailed to the checkout root. The path is resolved and checked to be
inside it, so `../../.env` and a symlink out both fail: this runs
unattended against a repository the user supplied, which is not the same
thing as a repository the user trusts.
"""

import json
import logging
import subprocess
from pathlib import Path

from langchain_core.tools import BaseTool, tool
from sqlalchemy.orm import Session

from app import models
from app.ingest.heuristic import CheckProposal

logger = logging.getLogger("dpm.maintenance.tools")

MAX_FILE_BYTES = 60_000
MAX_GREP_MATCHES = 40


def _safe_path(root: Path, relative: str) -> Path | None:
    """Resolve inside the checkout, or None.

    `resolve()` on both sides collapses `..` and follows symlinks before the
    comparison, so neither traversal nor a link planted in the repo escapes.
    """
    try:
        candidate = (root / relative).resolve()
        candidate.relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    return candidate if candidate.is_file() else None


def _check_summary(check: models.Check) -> dict:
    return {
        "check_id": check.id,
        "name": check.name,
        "type": check.type,
        "schedule": check.schedule,
        "enabled": check.enabled,
        "config": check.config,
        "origin": check.origin,
        # The two fields that decide whether this check may be touched.
        "human_edited": check.human_edited_at is not None,
        "agent_may_rewrite": check.agent_may_rewrite,
        "derived_from": check.derived_from,
        "derived_at_commit": check.derived_at_commit,
    }


def build_tools(
    db: Session,
    project: models.Project,
    checkout_root: Path,
    derived: list[CheckProposal],
    changed_paths: list[str],
    diff_text: str,
) -> list[BaseTool]:
    """Tools bound to one project and one detected change."""

    @tool
    def get_current_checks() -> str:
        """Every check that exists on this project right now, with its
        origin and whether a person has edited it.

        `agent_may_rewrite` is false for any check a human wrote or touched.
        Do not propose UPDATE or RETIRE for those unless the object they
        point at is gone."""
        return json.dumps(
            [
                _check_summary(check)
                for database in project.databases
                for check in database.checks
            ],
            default=str,
        )

    @tool
    def get_freshly_derived_checks() -> str:
        """What the rules would generate from the DDL as it stands now.

        This is the comparison set, not a proposal. Where it agrees with an
        existing check, nothing needs to change; where it differs, you decide
        whether the difference is a rename, a replacement, or noise."""
        return json.dumps(
            [
                {
                    "key": proposal["key"],
                    "name": proposal["name"],
                    "type": proposal["type"],
                    "schedule": proposal["schedule"],
                    "database": proposal["database"],
                    "config": proposal["config"],
                    "rationale": proposal["rationale"],
                    "concerns": proposal["concerns"],
                }
                for proposal in derived
            ],
            default=str,
        )

    @tool
    def get_change_diff() -> str:
        """The commit diff that triggered this review, and which watched
        files it touched.

        Tells you what changed. To understand what it *means*, read the file
        with read_repo_file - a diff shows a column moving, the file shows
        what the MERGE now maps into it."""
        return json.dumps(
            {
                "changed_paths": changed_paths,
                "diff": diff_text[:40_000],
                "truncated": len(diff_text) > 40_000,
            }
        )

    @tool
    def read_repo_file(path: str) -> str:
        """Read one file from the repository, by its repo-relative path.

        Use this on the files the diff named. The DDL is the authority on
        what a check should assert - particularly the MERGE, whose ON clause
        states the key and whose SELECT states the column mapping."""
        resolved = _safe_path(checkout_root, path)
        if resolved is None:
            return json.dumps({"error": f"No readable file at {path!r} inside the repository"})
        try:
            content = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            return json.dumps({"error": str(error)})
        return json.dumps(
            {
                "path": path,
                "content": content[:MAX_FILE_BYTES],
                "truncated": len(content) > MAX_FILE_BYTES,
            }
        )

    @tool
    def grep_repo(pattern: str) -> str:
        """Search the repository for a fixed string, returning matching
        lines with their files.

        Useful for tracing a column name: searching for the old name shows
        whether it survives anywhere, which is what separates a rename from
        a removal."""
        try:
            result = subprocess.run(
                ["git", "grep", "-n", "--fixed-strings", "--", pattern],
                cwd=checkout_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (subprocess.SubprocessError, OSError) as error:
            return json.dumps({"error": str(error)})
        # git grep exits 1 for "no matches", which is an answer, not a failure.
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        return json.dumps(
            {
                "pattern": pattern,
                "match_count": len(lines),
                "matches": lines[:MAX_GREP_MATCHES],
                "truncated": len(lines) > MAX_GREP_MATCHES,
            }
        )

    return [
        get_current_checks,
        get_freshly_derived_checks,
        get_change_diff,
        read_repo_file,
        grep_repo,
    ]
