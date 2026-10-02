from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.models.schemas import (
    HybridSearchRequest,
    HybridSearchResponse,
    RAGAskRequest,
    RAGAskResponse,
    RAGDeleteResponse,
    RAGIndexStatusResponse,
    RAGSearchRequest,
    RAGSearchResponse,
    RAGSyncResponse,
    RAGSyncSingleResponse,
)
from app.services.auth_service import verify_api_key
from app.services.hybrid_search_service import HybridSearchService
from app.services.rag_ingestion_service import get_chunk_store
from app.services.rag_ingestion_service import RAGIngestionService
from app.services.rag_query_service import RAGQueryService
from app.services.sync_state_service import load_sync_state
from app.core.config import get_settings

router = APIRouter(prefix="/rag", tags=["rag"], dependencies=[Depends(verify_api_key)])


@router.get("/index-status", response_model=RAGIndexStatusResponse)
async def rag_index_status() -> RAGIndexStatusResponse:
    """Index status (chunk count, last sync) from GCS/shared state. Poll to see if full sync finished."""
    state = load_sync_state()
    store = get_chunk_store()
    indexed_skus = {c.sku for c in store.values()}
    return RAGIndexStatusResponse(
        chunk_count=len(store),
        indexed_sku_count=len(indexed_skus),
        last_successful_sync_at=state.get("last_successful_sync_at"),
        last_run_at=state.get("last_run_at"),
        last_error=state.get("last_error"),
    )


@router.post("/sync", response_model=RAGSyncResponse)
async def sync_all_products() -> RAGSyncResponse:
    svc = RAGIngestionService()
    try:
        return await svc.sync_all()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Sync failed: {exc}")


@router.post("/sync/{sku}", response_model=RAGSyncSingleResponse)
async def sync_single_product(sku: str) -> RAGSyncSingleResponse:
    svc = RAGIngestionService()
    try:
        return await svc.sync_product(sku)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Sync failed: {exc}")


@router.delete("/product/{sku}", response_model=RAGDeleteResponse)
async def delete_product_vectors(sku: str) -> RAGDeleteResponse:
    svc = RAGIngestionService()
    try:
        return await svc.delete_product(sku)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Delete failed: {exc}")


@router.post("/search", response_model=RAGSearchResponse)
async def rag_search(body: RAGSearchRequest) -> RAGSearchResponse:
    svc = RAGQueryService()
    try:
        return await svc.search(query=body.query, top_k=body.top_k)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Search failed: {exc}")


@router.post("/ask", response_model=RAGAskResponse)
async def rag_ask(body: RAGAskRequest) -> RAGAskResponse:
    svc = RAGQueryService()
    try:
        return await svc.ask(
            query=body.query, top_k=body.top_k, temperature=body.temperature,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Ask failed: {exc}")


@router.post("/hybrid-search", response_model=HybridSearchResponse)
async def hybrid_search(body: HybridSearchRequest) -> HybridSearchResponse:
    svc = HybridSearchService()
    settings = get_settings()
    try:
        results = await svc.search(
            query=body.query, top_k=body.top_k, filters=body.filters,
        )
        return HybridSearchResponse(
            query=body.query,
            results=results,
            dense_weight=settings.HYBRID_DENSE_WEIGHT,
            sparse_weight=settings.HYBRID_SPARSE_WEIGHT,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Hybrid search failed: {exc}")
