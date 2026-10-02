"""Admin-only routes: AI evaluation monitoring, summary, failures, run, export, merchandising."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.models.schemas import (
    AIEvalListResponse,
    AIEvalRunRequest,
    AIEvalRunResponse,
    AIEvalSummaryResponse,
    AIEvaluationRecord,
    APIResponse,
    MerchandisingRuleListResponse,
    RAGFullRebuildResponse,
)
from app.repositories import ai_evaluation_repository as repo
from app.services.auth_service import verify_admin_api_key
from app.services.ai_evaluation_service import run_evaluation_batch

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(verify_admin_api_key)])


# ── Chart data ───────────────────────────────────────────────────────────────


@router.get("/ai-evals/chart-data")
async def get_ai_evals_chart_data(limit: int = Query(200, ge=1, le=2000)) -> dict[str, Any]:
    """Time-series and distribution data for dashboard charts."""
    repo.ensure_table()
    return {"success": True, "data": repo.get_chart_data(limit=limit)}


# ── Filter options ───────────────────────────────────────────────────────────


@router.get("/ai-evals/filters")
async def get_ai_evals_filters() -> dict[str, Any]:
    """Distinct model names, prompt versions, categories for filter dropdowns."""
    repo.ensure_table()
    return {"success": True, "data": repo.get_distinct_filters()}


# ── Worst / best queries ────────────────────────────────────────────────────


@router.get("/ai-evals/worst")
async def get_ai_evals_worst(limit: int = Query(5, ge=1, le=20)) -> dict[str, Any]:
    repo.ensure_table()
    return {"success": True, "data": [r.model_dump() for r in repo.get_worst_queries(limit)]}


@router.get("/ai-evals/best")
async def get_ai_evals_best(limit: int = Query(5, ge=1, le=20)) -> dict[str, Any]:
    repo.ensure_table()
    return {"success": True, "data": [r.model_dump() for r in repo.get_best_queries(limit)]}


# ── Summary (must be before /{id} so path order is correct) ───────────────────


@router.get("/ai-evals/summary", response_model=AIEvalSummaryResponse)
async def get_ai_evals_summary() -> AIEvalSummaryResponse:
    """Aggregated metrics for dashboard: counts, average scores, common failures."""
    stats = repo.get_summary_stats()
    return AIEvalSummaryResponse(
        success=True,
        total_tests=stats["total_tests"],
        average_total_score=stats.get("average_total_score"),
        average_intent_score=stats.get("average_intent_score"),
        average_category_score=stats.get("average_category_score"),
        average_stock_score=stats.get("average_stock_score"),
        average_relevance_score=stats.get("average_relevance_score"),
        average_styling_score=stats.get("average_styling_score"),
        average_memory_score=stats.get("average_memory_score"),
        average_format_score=stats.get("average_format_score"),
        average_latency_ms=stats.get("average_latency_ms"),
        pass_count=stats["pass_count"],
        warning_count=stats["warning_count"],
        fail_count=stats["fail_count"],
        fail_percentage=stats.get("fail_percentage"),
        most_common_fail_reasons=stats.get("most_common_fail_reasons", []),
        most_common_wrong_categories=stats.get("most_common_wrong_categories", []),
        most_common_out_of_stock=stats.get("most_common_out_of_stock", []),
        most_common_hallucinations=stats.get("most_common_hallucinations", []),
    )


# ── Failures ──────────────────────────────────────────────────────────────────


@router.get("/ai-evals/failures", response_model=AIEvalListResponse)
async def get_ai_evals_failures(
    max_score: float = Query(60.0, alias="max_score", description="Include records with total_score < this"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> AIEvalListResponse:
    """List evaluation records that failed or scored below threshold."""
    repo.ensure_table()
    items, total = repo.list_failures(max_total_score=max_score, page=page, page_size=page_size)
    total_pages = (total + page_size - 1) // page_size if page_size else 0
    return AIEvalListResponse(
        success=True,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# ── Run evaluation ───────────────────────────────────────────────────────────


@router.post("/ai-evals/run", response_model=AIEvalRunResponse)
async def post_ai_evals_run(body: AIEvalRunRequest | None = None) -> AIEvalRunResponse:
    """Run benchmark: execute test queries through the assistant, evaluate, and persist results."""
    repo.ensure_table()
    req = body or AIEvalRunRequest()
    result = await run_evaluation_batch(
        test_queries=req.test_queries,
        dataset_name=req.dataset_name,
        model_name=req.model_name,
        prompt_version=req.prompt_version,
        limit=req.limit,
        category_filter=req.category_filter,
        dry_run=req.dry_run,
    )
    return AIEvalRunResponse(
        success=True,
        run_id=result.get("run_id"),
        total_run=result["total_run"],
        saved_count=result["saved_count"],
        dry_run=result["dry_run"],
        summary=result.get("summary", {}),
    )


# ── Export ───────────────────────────────────────────────────────────────────


@router.get("/ai-evals/export")
async def get_ai_evals_export(
    format: str = Query("json", alias="format"),
    status: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    limit: int = Query(10000, ge=1, le=50000),
):
    """Export evaluation records as JSON or CSV for offline analysis."""
    repo.ensure_table()
    records = repo.get_all_for_export(status=status, date_from=date_from, date_to=date_to, limit=limit)
    if format.lower() == "csv":
        buf = io.StringIO()
        if not records:
            buf.write("id,created_at,test_query,evaluation_status,total_score\n")
        else:
            r0 = records[0]
            fieldnames = list(r0.model_dump().keys())
            writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for r in records:
                row = r.model_dump()
                for k, v in row.items():
                    if v is None:
                        row[k] = ""
                    elif isinstance(v, list):
                        if v and isinstance(v[0], dict):
                            row[k] = json.dumps(v, ensure_ascii=False)[:2000]
                        else:
                            row[k] = "; ".join(str(x) for x in v)
                    elif isinstance(v, dict):
                        row[k] = json.dumps(v, ensure_ascii=False)[:1000]
                writer.writerow(row)
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=ai_evaluations.csv"},
        )
    return {"success": True, "count": len(records), "data": [r.model_dump() for r in records]}


# ── Detail by id (path param) ──────────────────────────────────────────────────


@router.get("/ai-evals/{eval_id}")
async def get_ai_eval_by_id(eval_id: int) -> dict[str, Any]:
    """Full evaluation record for a single run: query, response, scores, validations, raw judge output."""
    repo.ensure_table()
    record = repo.get_evaluation_by_id(eval_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Evaluation {eval_id} not found")
    return {"success": True, "data": record.model_dump()}


# ── List (with filters and pagination) ────────────────────────────────────────


@router.get("/ai-evals", response_model=AIEvalListResponse)
async def get_ai_evals(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, description="pass | warning | fail"),
    min_score: float | None = Query(None),
    max_score: float | None = Query(None),
    category: str | None = Query(None),
    query_search: str | None = Query(None, alias="q"),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    model_name: str | None = Query(None),
    prompt_version: str | None = Query(None),
) -> AIEvalListResponse:
    """List evaluation records with filtering and pagination for admin table."""
    repo.ensure_table()
    items, total = repo.list_evaluations(
        page=page,
        page_size=page_size,
        status=status,
        min_score=min_score,
        max_score=max_score,
        category=category,
        query_search=query_search,
        date_from=date_from,
        date_to=date_to,
        model_name=model_name,
        prompt_version=prompt_version,
    )
    total_pages = (total + page_size - 1) // page_size if page_size else 0
    return AIEvalListResponse(
        success=True,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Merchandising Rules CRUD
# ══════════════════════════════════════════════════════════════════════════════


class MerchandisingRuleCreateRequest(BaseModel):
    rule_type: str
    value: str
    boost_weight: float = 0.1
    label: str | None = None
    active: bool = True
    starts_at: str | None = None
    ends_at: str | None = None


class MerchandisingRuleUpdateRequest(BaseModel):
    rule_type: str | None = None
    value: str | None = None
    boost_weight: float | None = None
    label: str | None = None
    active: bool | None = None
    starts_at: str | None = None
    ends_at: str | None = None


@router.get("/merchandising/rules", response_model=MerchandisingRuleListResponse)
def list_merchandising_rules(
    active_only: bool = Query(False, description="Return only active rules"),
) -> MerchandisingRuleListResponse:
    from app.services.merchandising_service import MerchandisingService
    rules = MerchandisingService().list_rules(active_only=active_only)
    return MerchandisingRuleListResponse(success=True, rules=rules)


@router.post("/merchandising/rules")
def create_merchandising_rule(body: MerchandisingRuleCreateRequest) -> dict[str, Any]:
    from app.services.merchandising_service import MerchandisingService
    try:
        rule = MerchandisingService().create_rule(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"success": True, "rule": rule}


@router.patch("/merchandising/rules/{rule_id}")
def update_merchandising_rule(rule_id: int, body: MerchandisingRuleUpdateRequest) -> dict[str, Any]:
    from app.services.merchandising_service import MerchandisingService
    data = body.model_dump(exclude_none=True)
    result = MerchandisingService().update_rule(rule_id, data)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
    return {"success": True, "rule": result}


@router.delete("/merchandising/rules/{rule_id}")
def delete_merchandising_rule(rule_id: int) -> dict[str, Any]:
    from app.services.merchandising_service import MerchandisingService
    deleted = MerchandisingService().delete_rule(rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
    return {"success": True, "deleted": True, "rule_id": rule_id}


# ── RAG (GCP: Vertex + GCS chunk store) ───────────────────────────────────────


@router.post("/rag/full-rebuild", response_model=RAGFullRebuildResponse)
async def admin_rag_full_rebuild() -> RAGFullRebuildResponse:
    """Clear all indexed chunks (Vertex vectors + chunk_store.json), then full Magento sync.

    Use after catalog-wide changes or to fix a corrupted index. Can take many minutes;
    ensure the HTTP client / Cloud Run request timeout is high enough for your catalog size.
    """
    from app.services.rag_ingestion_service import RAGIngestionService

    try:
        return await RAGIngestionService().full_rebuild_from_scratch()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Full rebuild failed: {exc}") from exc
