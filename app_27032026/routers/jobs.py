from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.models.schemas import (
    APIResponse,
    JobCreateResponse,
    JobListResponse,
    JobSchema,
    JobType,
)
from app.services.auth_service import verify_api_key
from app.services.job_service import get_job_registry, schedule_background_job

router = APIRouter(prefix="/rag/jobs", tags=["jobs"], dependencies=[Depends(verify_api_key)])


@router.post("/sync-all", response_model=JobCreateResponse)
async def create_full_sync_job() -> JobCreateResponse:
    async def _do_full_sync() -> dict[str, Any]:
        from app.services.rag_ingestion_service import RAGIngestionService
        from app.services.cache_service import CacheService

        svc = RAGIngestionService()
        result = await svc.sync_all()
        CacheService().invalidate_all_products()
        return result.model_dump()

    job = schedule_background_job(JobType.FULL_SYNC, _do_full_sync)
    return JobCreateResponse(job=job)


@router.post("/sync-product/{sku}", response_model=JobCreateResponse)
async def create_single_sync_job(sku: str) -> JobCreateResponse:
    async def _do_single_sync() -> dict[str, Any]:
        from app.services.rag_ingestion_service import RAGIngestionService
        from app.services.cache_service import CacheService

        svc = RAGIngestionService()
        result = await svc.sync_product(sku)
        CacheService().invalidate_product(sku)
        return result.model_dump()

    job = schedule_background_job(JobType.SINGLE_SYNC, _do_single_sync, sku=sku)
    return JobCreateResponse(job=job)


@router.get("", response_model=JobListResponse)
async def list_jobs() -> JobListResponse:
    registry = get_job_registry()
    return JobListResponse(jobs=registry.list_all())


@router.get("/{job_id}", response_model=JobCreateResponse)
async def get_job(job_id: str) -> JobCreateResponse:
    registry = get_job_registry()
    job = registry.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return JobCreateResponse(job=job)


@router.post("/{job_id}/cancel", response_model=APIResponse)
async def cancel_job(job_id: str) -> APIResponse:
    registry = get_job_registry()
    cancelled = registry.cancel(job_id)
    if not cancelled:
        raise HTTPException(
            status_code=400,
            detail=f"Job {job_id} cannot be cancelled (not found or already finished)",
        )
    return APIResponse(success=True)
