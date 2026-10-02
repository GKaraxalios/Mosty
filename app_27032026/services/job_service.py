"""Background job registry and async worker.

In-memory implementation — designed to be replaced by Celery / RQ / Cloud Tasks
by swapping the registry backend.
"""
from __future__ import annotations

import asyncio
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

from app.core.logging import get_logger
from app.models.schemas import JobSchema, JobStatus, JobType

logger = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Job Registry (in-memory, replaceable) ────────────────────────────────────


class JobRegistry:
    """Thread-safe in-memory job store."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobSchema] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def create(self, job_type: JobType, sku: str | None = None) -> JobSchema:
        job = JobSchema(
            job_id=uuid.uuid4().hex[:12],
            type=job_type,
            status=JobStatus.PENDING,
            created_at=_now_iso(),
            sku=sku,
        )
        self._jobs[job.job_id] = job
        logger.info("Job created: id=%s type=%s", job.job_id, job.type)
        return job

    def get(self, job_id: str) -> JobSchema | None:
        return self._jobs.get(job_id)

    def list_all(self, limit: int = 50) -> list[JobSchema]:
        jobs = sorted(
            self._jobs.values(), key=lambda j: j.created_at, reverse=True
        )
        return jobs[:limit]

    def update(self, job_id: str, **kwargs: Any) -> JobSchema | None:
        job = self._jobs.get(job_id)
        if not job:
            return None
        for key, val in kwargs.items():
            if hasattr(job, key):
                setattr(job, key, val)
        return job

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        if job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
            return False
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
        job.status = JobStatus.CANCELLED
        job.finished_at = _now_iso()
        logger.info("Job cancelled: %s", job_id)
        return True

    def register_task(self, job_id: str, task: asyncio.Task[None]) -> None:
        self._tasks[job_id] = task


_registry: JobRegistry | None = None


def get_job_registry() -> JobRegistry:
    global _registry
    if _registry is None:
        _registry = JobRegistry()
    return _registry


# ── Background worker ────────────────────────────────────────────────────────


async def run_job_in_background(
    job_id: str,
    coro_factory: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
    **kwargs: Any,
) -> None:
    """Execute *coro_factory* in the background, updating the job registry."""
    registry = get_job_registry()
    job = registry.get(job_id)
    if not job:
        return

    registry.update(job_id, status=JobStatus.RUNNING, started_at=_now_iso())
    logger.info("Job started: %s", job_id)

    try:
        result = await coro_factory(**kwargs)
        registry.update(
            job_id,
            status=JobStatus.COMPLETED,
            finished_at=_now_iso(),
            summary=str(result.get("summary", "Done")),
            total_products=result.get("products_synced", 0),
            processed_products=result.get("products_synced", 0),
            total_chunks=result.get("chunks_created", 0),
            processed_chunks=result.get("chunks_created", 0),
        )
        logger.info("Job completed: %s", job_id)
    except asyncio.CancelledError:
        registry.update(
            job_id, status=JobStatus.CANCELLED, finished_at=_now_iso()
        )
        logger.info("Job cancelled during execution: %s", job_id)
    except Exception as exc:
        tb = traceback.format_exc()
        registry.update(
            job_id,
            status=JobStatus.FAILED,
            finished_at=_now_iso(),
            errors=[str(exc)],
            summary=f"Failed: {exc}",
        )
        logger.exception("Job failed: %s — %s", job_id, exc)


def schedule_background_job(
    job_type: JobType,
    coro_factory: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
    sku: str | None = None,
    **kwargs: Any,
) -> JobSchema:
    """Create a job, schedule it as an asyncio Task, and return the job metadata."""
    registry = get_job_registry()
    job = registry.create(job_type, sku=sku)

    task = asyncio.create_task(
        run_job_in_background(job.job_id, coro_factory, **kwargs)
    )
    registry.register_task(job.job_id, task)
    return job
