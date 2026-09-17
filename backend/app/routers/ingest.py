"""Setting a project up from the repository that defines its pipeline.

Three calls, in the order the user meets them:

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
from sqlalchemy.orm import Session

from app import models, schemas
from app.connectors.security import encrypt_config_secrets
from app.connectors.snowflake_connector import test_snowflake_connection
from app.db import get_db
from app.ingest import jobs
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

    job_id = jobs.start_analysis(repo_url, payload.token, payload.ref)
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
