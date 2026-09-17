"""Resolves a Snowflake object to the file in a project's repository that defines it.

This used to be a module-level dict of this repo's own `snowflake/` paths, which
meant RCA could only ever explain the one pipeline the app ships with. The map
is now per-project data, built when a repository is ingested and stored on each
`Database` row as `repo_paths` ({"BRONZE": "snowflake/.../bronze.sql"}).

The lookup key stays DATABASE.SCHEMA rather than the full object name: a schema
is defined by one file, and asking callers to register every table would mean
the map goes stale the first time someone adds a table without re-ingesting.
"""

from app import models


class RepoContext:
    """Where a project's pipeline is defined, and which file defines what.

    `root` is None for a project that was not created from a repository. RCA
    then skips git evidence rather than guessing at a checkout, because the
    wrong repo's history is worse evidence than no history at all.
    """

    def __init__(self, root: str | None, schema_paths: dict[str, str]):
        self.root = root
        self.schema_paths = schema_paths

    def resolve(self, fully_qualified_object: str) -> str | None:
        parts = fully_qualified_object.split(".")
        if len(parts) < 2:
            return None
        return self.schema_paths.get(f"{parts[0]}.{parts[1]}".upper())


EMPTY_REPO_CONTEXT = RepoContext(None, {})


def build_repo_context(check: "models.Check") -> RepoContext:
    """Collects the repo mapping for every database in the check's project.

    Scoped to the project, not to the check's own database, because a check can
    legitimately reference sibling databases - a silver-vs-gold parity check
    spans two - and RCA has to be able to attribute either side.
    """
    project = check.database.project
    schema_paths: dict[str, str] = {}
    for database in project.databases:
        for schema_name, file_path in (database.repo_paths or {}).items():
            schema_paths[f"{database.name}.{schema_name}".upper()] = file_path
    return RepoContext(project.repo_path, schema_paths)
