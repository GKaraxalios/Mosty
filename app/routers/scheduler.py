from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.models.schemas import APIResponse, SchedulerStatusResponse
from app.services.auth_service import verify_api_key
from app.services.scheduler_service import (
    get_scheduler_status,
    trigger_full_sync_now,
    trigger_incremental_sync_now,
)

router = APIRouter(
    prefix="/rag/scheduler",
    tags=["scheduler"],
    dependencies=[Depends(verify_api_key)],
)


@router.get("/status", response_model=SchedulerStatusResponse)
async def scheduler_status() -> SchedulerStatusResponse:
    status = get_scheduler_status()
    return SchedulerStatusResponse(**status)


@router.post("/run-now/full", response_model=APIResponse)
async def run_full_sync_now() -> APIResponse:
    try:
        await trigger_full_sync_now()
        return APIResponse(success=True)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/run-now/incremental", response_model=APIResponse)
async def run_incremental_sync_now() -> APIResponse:
    try:
        await trigger_incremental_sync_now()
        return APIResponse(success=True)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))
