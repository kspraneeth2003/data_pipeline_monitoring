"""Profile jobs, on the same in-process scheduler the checks use.

Registered alongside the check jobs rather than inside `app/scheduler.py`'s
sync loop, so profiling can be added without touching how checks are
scheduled. The two loops own disjoint job-id prefixes (`check:` and
`profile:`), and each only ever removes its own.
"""

import logging

from apscheduler.triggers.cron import CronTrigger

from app.db import SessionLocal
from app.profiling.models import ProfileTarget
from app.profiling.service import run_profile
from app.scheduler import scheduler

logger = logging.getLogger("dpm.profiling")

PREFIX = "profile:"

# APScheduler drops a job that starts more than `misfire_grace_time` late,
# and its default is one second - silently, with only a log warning. Every
# hourly profile fires at minute 0, alongside the checks, so the worker pool
# is busy exactly then and most profiles were being skipped. A profile that
# runs a minute late is fine; one that never runs is not. `None` means "run
# however late", and coalescing keeps a backlog to one pending run per table.
_JOB_OPTIONS = {"misfire_grace_time": None, "coalesce": True, "max_instances": 1}


def _run_profile_job(target_id: str) -> None:
    db = SessionLocal()
    try:
        run = run_profile(db, target_id)
        logger.info("Profile %s finished: %s", target_id, run.status)
    except Exception:
        logger.exception("Profile %s threw an error", target_id)
    finally:
        db.close()


def _sync_profile_jobs() -> None:
    db = SessionLocal()
    try:
        targets = db.query(ProfileTarget).filter_by(enabled=True).all()
        wanted = {t.id: t for t in targets}
        for target in targets:
            job_id = f"{PREFIX}{target.id}"
            job = scheduler.get_job(job_id)
            # Re-add when the schedule changed, not only when missing -
            # otherwise editing a schedule would silently keep the old one.
            if job and getattr(job, "name", None) == target.schedule and job.misfire_grace_time is None:
                continue
            try:
                trigger = CronTrigger.from_crontab(target.schedule)
            except ValueError:
                logger.error("Skipping profile of %s: invalid cron %r", target.object, target.schedule)
                continue
            scheduler.add_job(
                _run_profile_job, trigger, args=[target.id], id=job_id, name=target.schedule,
                replace_existing=True, **_JOB_OPTIONS,
            )
        for job in scheduler.get_jobs():
            if job.id.startswith(PREFIX) and job.id.removeprefix(PREFIX) not in wanted:
                scheduler.remove_job(job.id)
    finally:
        db.close()


def start_profiling_jobs() -> None:
    if not scheduler.running or scheduler.get_job("sync_profile_jobs"):
        return
    scheduler.add_job(_sync_profile_jobs, "interval", seconds=30, id="sync_profile_jobs")
    # Profiling must never be able to stop the app starting. Before the
    # migration has run, its tables do not exist, and raising here would take
    # the checks, incidents and every other route down with it. The interval
    # job retries every 30 seconds and picks up once the tables are there.
    try:
        _sync_profile_jobs()
    except Exception:
        logger.exception("Profiling jobs not scheduled yet - has `alembic upgrade head` been run?")


def queue_now(target_ids: list[str]) -> int:
    """Profile these targets immediately, in the background. Returns how many were queued.

    "Profile all" used to add tables to the hourly schedule and stop there,
    so they sat as "queued" until the next top of the hour - which reads as
    broken. One-off jobs on the same scheduler run them now, a few at a time
    (the scheduler's worker pool bounds how many Snowflake sessions open at
    once), without holding the HTTP request open for the whole batch. Their
    ids are distinct from the hourly `profile:` jobs, so neither replaces the
    other. With the scheduler not running (tests, a one-off script) nothing
    is queued and the hourly schedule remains the fallback.
    """
    if not scheduler.running:
        return 0
    for target_id in target_ids:
        scheduler.add_job(
            _run_profile_job, args=[target_id], id=f"profile-now:{target_id}", replace_existing=True, **_JOB_OPTIONS
        )
    return len(target_ids)
