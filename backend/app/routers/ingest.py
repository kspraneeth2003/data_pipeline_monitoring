"""Setting a project up from the repository that defines its pipeline.

Three calls, in the order the user meets them:

    GET  /api/github/status       is GitHub set up, and what has been granted
    GET  /api/github/repositories the repositories the user granted read access to
    GET  /api/github/callback     where GitHub returns after the consent screen

    POST /api/ingest              start analysing a repository  -> job id
    GET  /api/ingest/{job_id}     poll for progress and the proposal
    POST /api/ingest/{job_id}/project   confirm it into a real project

Credentials only appear at the third step. The repository describes the
pipeline's structure, and that is knowable without touching Snowflake - so
making the user find credentials before they can see what the tool found would
be a gate with nothing behind it.

The third call is the only one that writes, and it writes everything at once
after testing the connection, so a project never exists half-configured.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import models, schemas
from app.connectors.security import encrypt_config_secrets
from app.connectors.snowflake_connector import test_snowflake_connection
from app.db import get_db
from app.config import settings
from app.ingest import github, jobs
from app.ingest.repo import RepoError, normalize_repo_url
from app.routers.projects import (
    _project_out,
    _slugify,
    _unique_database_slug,
    _unique_project_slug,
)

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


@router.post("", response_model=schemas.RepoIngestJobOut, status_code=202)
def start_ingestion(payload: schemas.RepoIngestRequest, db: Session = Depends(get_db)):
    try:
        repo_url = normalize_repo_url(payload.repo_url)
    except RepoError as error:
        raise HTTPException(400, str(error)) from error

    job_id = jobs.start_analysis(
        repo_url, payload.token, payload.ref, payload.installation_id
    )
    job = jobs.get_job(job_id)
    assert job is not None
    return job


@router.get("/{job_id}", response_model=schemas.RepoIngestJobOut)
def get_ingestion(job_id: str):
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "That analysis has expired - start it again.")
    return job


@router.delete("/{job_id}", status_code=204)
def discard_ingestion(job_id: str):
    jobs.drop_job(job_id)


@router.post("/{job_id}/project", response_model=schemas.ProjectWithHealthOut, status_code=201)
def create_project_from_analysis(
    job_id: str, payload: schemas.RepoProjectCreate, db: Session = Depends(get_db)
):
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "That analysis has expired - start it again.")
    if job["status"] != "DONE" or not job["analysis"]:
        raise HTTPException(409, "That analysis has not finished yet.")

    analysis = job["analysis"]

    if payload.connector_type == "SNOWFLAKE":
        if not payload.config.get("password") and not payload.config.get("privateKey"):
            raise HTTPException(400, "Provide either a password or a private key")
        try:
            test_snowflake_connection(payload.config)
        except Exception as error:  # noqa: BLE001
            raise HTTPException(400, f"Connection test failed: {error}") from error

    selected_databases = _selected(payload.databases, [d["name"] for d in analysis["databases"]])
    selected_checks = _selected(payload.checks, [c["key"] for c in analysis["checks"]])
    if not selected_databases:
        raise HTTPException(400, "Select at least one database.")

    project = models.Project(
        slug=_unique_project_slug(db, _slugify(payload.name)),
        name=payload.name,
        description=payload.description,
        repo_url=analysis["repo_url"],
        repo_ref=analysis["repo_ref"],
        repo_commit=analysis["repo_commit"],
        repo_path=analysis["repo_path"],
    )
    db.add(project)
    db.flush()

    connector = models.Connector(
        project_id=project.id,
        name=payload.connector_name or "snowflake",
        type=payload.connector_type,
        config=encrypt_config_secrets(payload.connector_type, payload.config),
        comment=f"Created from {analysis['repo_url']}.",
    )
    db.add(connector)
    db.flush()

    databases_by_name: dict[str, models.Database] = {}
    for proposed in analysis["databases"]:
        if proposed["name"] not in selected_databases:
            continue
        database = models.Database(
            project_id=project.id,
            connector_id=connector.id,
            name=proposed["name"],
            slug=_unique_database_slug(db, project.id, _slugify(proposed["name"])),
            description=proposed["description"],
            repo_paths=proposed["repo_paths"],
        )
        db.add(database)
        databases_by_name[proposed["name"]] = database
    db.flush()

    for proposed in analysis["checks"]:
        if proposed["key"] not in selected_checks:
            continue
        database = databases_by_name.get(proposed["database"])
        if database is None:
            # The check anchors to a database the user deselected. Dropping it
            # is the only coherent option - a check reaches its project through
            # its database, so one with no database has no place in the tree.
            continue
        db.add(
            models.Check(
                database_id=database.id,
                connector_id=connector.id,
                name=proposed["name"],
                description=proposed["description"],
                type=proposed["type"],
                schedule=proposed["schedule"],
                config=proposed["config"],
                # Provenance, recorded at the moment the check is created
                # because it cannot be reconstructed later. It is what lets
                # the maintenance agent know this check is its own to update
                # when the DDL moves - and, just as importantly, what marks
                # every *other* check as one it must not touch.
                origin=(
                    models.CheckOrigin.AGENT.value
                    if proposed.get("source") == "llm"
                    else models.CheckOrigin.DERIVED.value
                ),
                derived_from={
                    "key": proposed["key"],
                    "rationale": proposed["rationale"],
                    "source": proposed.get("source"),
                },
                derived_at_commit=analysis.get("repo_commit"),
            )
        )

    db.commit()
    db.refresh(project)

    jobs.drop_job(job_id)
    return _project_out(db, project)


def _selected(requested: list[str] | None, available: list[str]) -> set[str]:
    """`None` means "everything the analysis proposed"; a list is a filter.

    Unknown names are ignored rather than rejected: the client is filtering a
    proposal it was just handed, and failing the whole creation because one key
    drifted would lose the user's other selections for no benefit.
    """
    if requested is None:
        return set(available)
    return {name for name in requested if name in set(available)}


# --- GitHub App ----------------------------------------------------------

github_router = APIRouter(prefix="/api/github", tags=["github"])


@github_router.get("/status", response_model=schemas.GitHubStatusOut)
def github_status():
    """Whether GitHub is usable, and what has been granted so far.

    Never raises. A misconfigured or unreachable GitHub must not break the
    setup screen - the paste-a-URL path still works, and the screen needs to be
    able to say so rather than fail to render.
    """
    if not github.is_configured():
        return schemas.GitHubStatusOut(configured=False, install_url=None, installations=[])
    try:
        return schemas.GitHubStatusOut(
            configured=True,
            install_url=github.install_url(),
            installations=github.list_installations(),
        )
    except github.GitHubError as error:
        return schemas.GitHubStatusOut(
            configured=True,
            install_url=github.install_url(),
            installations=[],
            error=str(error),
        )


@github_router.get("/repositories", response_model=list[schemas.GitHubRepositoryOut])
def github_repositories():
    if not github.is_configured():
        raise HTTPException(400, "GitHub is not configured on this server.")
    try:
        return github.list_repositories()
    except github.GitHubError as error:
        raise HTTPException(502, str(error)) from error


@github_router.get("/callback")
def github_callback(installation_id: int | None = None, setup_action: str | None = None):
    """Where GitHub sends the browser after the user grants (or declines) access.

    Nothing is recorded here. GitHub is the source of truth for what was
    granted, and the next call to /repositories asks it directly - so this only
    has to put the user back where they were, with enough in the URL for the
    page to say what happened.
    """
    target = (
        f"{settings.frontend_base_url}/projects/new"
        f"?github={setup_action or 'install'}"
        f"{f'&installation_id={installation_id}' if installation_id else ''}"
    )
    return RedirectResponse(target, status_code=303)
