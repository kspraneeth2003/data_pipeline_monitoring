import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app import models
from app.checks.runner import execute_check
from app.config import settings
from app.db import SessionLocal
from app.maintenance.service import maintenance_job
from app.monitoring.sweep import sweep_job

logger = logging.getLogger("dpm.scheduler")
scheduler = BackgroundScheduler()


def _run_check_job(check_id: str, check_name: str) -> None:
    logger.info("Running check %s (%s)", check_name, check_id)
    db = SessionLocal()
    try:
        run_id = execute_check(db, check_id)
        logger.info("Check %s finished, run %s", check_name, run_id)
    except Exception:
        logger.exception("Check %s threw an error", check_name)
    finally:
        db.close()


def _sync_jobs() -> None:
    db = SessionLocal()
    try:
        checks = db.query(models.Check).filter_by(enabled=True).all()
        enabled_ids = {c.id for c in checks}

        for check in checks:
            job_id = f"check:{check.id}"
            if scheduler.get_job(job_id):
                continue
            try:
                trigger = CronTrigger.from_crontab(check.schedule)
            except ValueError:
                logger.error('Skipping check "%s" (%s): invalid cron "%s"', check.name, check.id, check.schedule)
                continue
            scheduler.add_job(_run_check_job, trigger, args=[check.id, check.name], id=job_id, replace_existing=True)
            logger.info('Scheduled "%s" (%s) with cron "%s"', check.name, check.id, check.schedule)

        for job in scheduler.get_jobs():
            if job.id.startswith("check:") and job.id.removeprefix("check:") not in enabled_ids:
                scheduler.remove_job(job.id)
                logger.info("Unscheduled job %s (disabled or deleted)", job.id)
    finally:
        db.close()


def start_scheduler() -> None:
    if scheduler.running:
        return
    scheduler.add_job(_sync_jobs, "interval", seconds=30, id="sync_jobs")
    # The monitor's heartbeat. Separate from the per-check jobs because it is
    # about incidents rather than checks: it still has work to do on a day
    # when nothing runs at all, which is exactly when an unanswered ticket
    # most needs escalating.
    scheduler.add_job(
        sweep_job,
        "interval",
        seconds=settings.monitor_interval_seconds,
        id="monitor_sweep",
        # A slow sweep must not stack up behind itself; one late pass is
        # recoverable, two concurrent ones double-comment every incident.
        max_instances=1,
        coalesce=True,
    )
    # Pipeline definitions change on the order of days, so this runs far
    # less often than the monitor - it costs a git fetch per project.
    scheduler.add_job(
        maintenance_job,
        "interval",
        seconds=settings.maintenance_interval_seconds,
        id="maintenance_scan",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    _sync_jobs()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
