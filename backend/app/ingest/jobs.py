"""Runs repository analysis in the background so the browser is not left hanging.

Analysis clones a repository and then asks an LLM to reason about it, which
takes on the order of a minute. Doing that inside the request would mean a
single POST held open for 90 seconds behind whatever proxy timeout is in the
way, with no way to show the user which step is running.

Jobs live in memory, not in Postgres, and that is deliberate: an analysis is a
*proposal*, not a record. Nothing it produces matters until the user confirms
it, and a half-finished analysis that survived a restart would be something to
explain and clean up rather than something to use. The cost is that `uvicorn
--reload` drops in-flight jobs - acceptable, since re-running costs a re-parse
and the git checkout is already cached.

This assumes one process, which the app already assumes elsewhere: APScheduler
runs in-process for the same reason.
"""

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Literal, TypedDict

from app.ingest.graph import analyze_repository
from app.ingest.repo import RepoError

logger = logging.getLogger(__name__)

JobStatus = Literal["RUNNING", "DONE", "ERROR"]

# An abandoned analysis should not pin a repo's worth of parsed DDL forever.
JOB_TTL_SECONDS = 60 * 60
MAX_CONCURRENT_ANALYSES = 2

STAGES = {
    "fetch": "Fetching the repository",
    "parse": "Reading the SQL",
    "heuristic": "Deriving checks from the DDL",
    "llm_enrich": "Asking the agent to refine them",
}


class Job(TypedDict):
    id: str
    status: JobStatus
    stage: str
    repo_url: str
    analysis: dict | None
    error: str | None
    created_at: float


_jobs: dict[str, Job] = {}
_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_ANALYSES, thread_name_prefix="ingest")


def _set(job_id: str, **fields) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job:
            job.update(fields)


def _evict_expired() -> None:
    cutoff = time.time() - JOB_TTL_SECONDS
    with _lock:
        for job_id in [j for j, job in _jobs.items() if job["created_at"] < cutoff]:
            del _jobs[job_id]


def start_analysis(repo_url: str, token: str | None, ref: str | None) -> str:
    _evict_expired()
    job_id = uuid.uuid4().hex
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "status": "RUNNING",
            "stage": STAGES["fetch"],
            "repo_url": repo_url,
            "analysis": None,
            "error": None,
            "created_at": time.time(),
        }

    def run() -> None:
        try:
            analysis = analyze_repository(
                repo_url,
                token,
                ref,
                on_stage=lambda name: _set(job_id, stage=STAGES.get(name, name)),
            )
            _set(job_id, status="DONE", stage="Done", analysis=analysis)
        except RepoError as error:
            # The user can fix this one - a bad URL, a private repo, a bad token.
            _set(job_id, status="ERROR", error=str(error))
        except Exception as error:  # noqa: BLE001
            logger.exception("Repository analysis failed for %s", repo_url)
            _set(job_id, status="ERROR", error=f"Analysis failed: {error}"[:400])

    _executor.submit(run)
    return job_id


def get_job(job_id: str) -> Job | None:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None  # type: ignore[return-value]


def drop_job(job_id: str) -> None:
    with _lock:
        _jobs.pop(job_id, None)
