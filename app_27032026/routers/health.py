from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from app.core.config import get_settings
from app.models.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        app=settings.APP_NAME,
        version=settings.APP_VERSION,
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        features={
            "cache": settings.CACHE_ENABLED,
            "scheduler": settings.ENABLE_SCHEDULER,
            "reranking": settings.RERANK_ENABLED,
            "memory": settings.MEMORY_ENABLED,
            "hybrid_search": True,
        },
    )
