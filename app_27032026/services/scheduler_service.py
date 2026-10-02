"""Scheduled ingestion via APScheduler.

Supports configurable full-sync (cron) and incremental-sync (interval).
Lock mechanism prevents overlapping runs.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import JobType

logger = get_logger(__name__)

_lock = asyncio.Lock()
_scheduler_instance: Any = None
_running = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _run_full_sync() -> None:
    if _lock.locked():
        logger.warning("Full sync skipped — another sync is already running")
        return
    async with _lock:
        logger.info("Scheduled full sync started")
        from app.services.job_service import schedule_background_job

        async def _do_full_sync() -> dict[str, Any]:
            from app.services.rag_ingestion_service import RAGIngestionService
            from app.services.cache_service import CacheService

            svc = RAGIngestionService()
            result = await svc.sync_all()
            CacheService().invalidate_all_products()
            return result.model_dump()

        schedule_background_job(JobType.FULL_SYNC, _do_full_sync)


async def _run_incremental_sync() -> None:
    if _lock.locked():
        logger.warning("Incremental sync skipped — another sync is already running")
        return
    async with _lock:
        logger.info("Scheduled incremental sync started")
        from app.services.job_service import schedule_background_job

        async def _do_incremental() -> dict[str, Any]:
            from app.services.incremental_index_service import IncrementalIndexService

            svc = IncrementalIndexService()
            result = await svc.run_incremental_sync(reconcile_deletes=False)
            return result

        schedule_background_job(JobType.INCREMENTAL_SYNC, _do_incremental)


async def start_scheduler() -> None:
    global _scheduler_instance, _running
    settings = get_settings()

    if not settings.ENABLE_SCHEDULER:
        logger.info("Scheduler disabled (ENABLE_SCHEDULER=false)")
        return

    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError:
        logger.warning("APScheduler not installed — scheduler disabled")
        return

    scheduler = AsyncIOScheduler()

    # Full sync — cron
    cron_parts = settings.FULL_SYNC_CRON.split()
    if len(cron_parts) == 5:
        trigger = CronTrigger(
            minute=cron_parts[0],
            hour=cron_parts[1],
            day=cron_parts[2],
            month=cron_parts[3],
            day_of_week=cron_parts[4],
        )
        scheduler.add_job(_run_full_sync, trigger, id="full_sync", name="Full product sync")
        logger.info("Scheduled full sync: %s", settings.FULL_SYNC_CRON)

    # Incremental sync — interval
    if settings.INCREMENTAL_SYNC_ENABLED:
        scheduler.add_job(
            _run_incremental_sync,
            IntervalTrigger(minutes=settings.INCREMENTAL_SYNC_INTERVAL_MINUTES),
            id="incremental_sync",
            name="Incremental product sync",
        )
        logger.info(
            "Scheduled incremental sync every %d minutes",
            settings.INCREMENTAL_SYNC_INTERVAL_MINUTES,
        )

    scheduler.start()
    _scheduler_instance = scheduler
    _running = True
    logger.info("Scheduler started")


async def stop_scheduler() -> None:
    global _scheduler_instance, _running
    if _scheduler_instance:
        _scheduler_instance.shutdown(wait=False)
        _scheduler_instance = None
        _running = False
        logger.info("Scheduler stopped")


def get_scheduler_status() -> dict[str, Any]:
    settings = get_settings()
    result: dict[str, Any] = {
        "enabled": settings.ENABLE_SCHEDULER,
        "running": _running,
        "full_sync_cron": settings.FULL_SYNC_CRON,
        "incremental_sync_interval_minutes": settings.INCREMENTAL_SYNC_INTERVAL_MINUTES,
        "incremental_sync_enabled": settings.INCREMENTAL_SYNC_ENABLED,
        "next_full_sync": None,
        "next_incremental_sync": None,
    }

    if _scheduler_instance and _running:
        for job in _scheduler_instance.get_jobs():
            nrt = job.next_run_time
            ts = nrt.strftime("%Y-%m-%dT%H:%M:%SZ") if nrt else None
            if job.id == "full_sync":
                result["next_full_sync"] = ts
            elif job.id == "incremental_sync":
                result["next_incremental_sync"] = ts

    return result


async def trigger_full_sync_now() -> None:
    await _run_full_sync()


async def trigger_incremental_sync_now() -> None:
    await _run_incremental_sync()
