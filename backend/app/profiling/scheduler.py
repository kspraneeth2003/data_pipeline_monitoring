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
            if job and getattr(job, "name", None) == target.schedule:
                continue
            try:
                trigger = CronTrigger.from_crontab(target.schedule)
            except ValueError:
                logger.error("Skipping profile of %s: invalid cron %r", target.object, target.schedule)
                continue
            scheduler.add_job(
                _run_profile_job, trigger, args=[target.id], id=job_id, name=target.schedule,
                replace_existing=True, max_instances=1, coalesce=True,
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
