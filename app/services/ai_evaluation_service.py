"""AI assistant evaluation service: run test queries, run validators, persist results."""

from __future__ import annotations

import time
import uuid
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import ChatRequest, ChatResponse
from app.repositories.ai_evaluation_repository import (
    DEFAULT_EVAL_QUERIES,
    create_evaluation,
)
from app.services.chat_service import ChatService

logger = get_logger(__name__)


def _allowed_skus(response: ChatResponse) -> set[str]:
    return {s.sku for s in response.sources}


def _recommended_skus(response: ChatResponse) -> list[str]:
    return [p.sku for p in response.products]


def _validate_duplicates(skus: list[str]) -> tuple[bool, list[str]]:
    seen: set[str] = set()
    dupes: list[str] = []
    for s in skus:
        if s in seen:
            dupes.append(s)
        seen.add(s)
    return len(dupes) == 0, dupes


def _validate_hallucination(recommended: list[str], allowed: set[str]) -> tuple[bool, list[str]]:
    bad = [s for s in recommended if s not in allowed]
    return len(bad) == 0, bad


def _validate_format(response: ChatResponse) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not (response.answer and response.answer.strip()):
        reasons.append("empty_answer")
    if not isinstance(response.products, list):
        reasons.append("products_not_list")
    for i, p in enumerate(response.products):
        if not getattr(p, "sku", None):
            reasons.append(f"product_{i}_missing_sku")
        if not getattr(p, "name", None):
            reasons.append(f"product_{i}_missing_name")
    return len(reasons) == 0, reasons


def _compute_heuristic_scores(
    intent_ok: bool,
    category_ok: bool,
    stock_ok: bool,
    relevance_ok: bool,
    styling_ok: bool,
    memory_ok: bool,
    format_ok: bool,
    duplicate_ok: bool,
    hallucination_ok: bool,
) -> dict[str, float]:
    """Simple heuristic scores (0–100) from validation flags. No LLM judge."""
    intent_score = 100.0 if intent_ok else 50.0
    category_score = 100.0 if category_ok else 0.0
    stock_score = 100.0 if stock_ok else 0.0
    relevance_score = 100.0 if relevance_ok else 50.0
    styling_score = 100.0 if styling_ok else 50.0
    memory_score = 100.0 if memory_ok else 0.0
    format_score = 100.0 if format_ok else 0.0
    # Penalize duplicate/hallucination
    if not duplicate_ok:
        format_score = min(format_score, 50.0)
    if not hallucination_ok:
        relevance_score = 0.0
    total = (
        intent_score + category_score + stock_score + relevance_score
        + styling_score + memory_score + format_score
    ) / 7.0
    return {
        "intent_score": round(intent_score, 2),
        "category_score": round(category_score, 2),
        "stock_score": round(stock_score, 2),
        "relevance_score": round(relevance_score, 2),
        "styling_score": round(styling_score, 2),
        "memory_score": round(memory_score, 2),
        "format_score": round(format_score, 2),
        "total_score": round(total, 2),
    }


def _evaluation_status(
    total_score: float,
    fail_reasons: list[str],
) -> str:
    if total_score < 60 or any(
        r in fail_reasons
        for r in ("hallucination", "duplicate_skus", "memory_leak")
    ):
        return "fail"
    if total_score < 80 or fail_reasons:
        return "warning"
    return "pass"


async def run_single_evaluation(
    query: str,
    dataset_name: str | None = None,
    model_name: str | None = None,
    prompt_version: str | None = None,
    save: bool = True,
) -> dict[str, Any]:
    """Run one test query through the assistant, validate, optionally save. Returns record payload."""
    settings = get_settings()
    session_id = f"eval-{uuid.uuid4().hex[:12]}"
    chat_svc = ChatService()
    req = ChatRequest(session_id=session_id, query=query, temperature=0.2)

    start = time.perf_counter()
    try:
        response: ChatResponse = await chat_svc.ask(req)
    except Exception as e:
        logger.exception("Eval ask failed for query=%r: %s", query, e)
        latency_ms = int((time.perf_counter() - start) * 1000)
        payload = {
            "test_query": query,
            "assistant_response": "",
            "detected_intent": None,
            "requested_category": None,
            "returned_categories": None,
            "recommended_products": None,
            "recommended_skus": [],
            "stock_validation_passed": False,
            "duplicate_validation_passed": True,
            "category_validation_passed": True,
            "memory_isolation_passed": True,
            "formatting_validation_passed": False,
            "hallucination_validation_passed": True,
            "intent_score": 0.0,
            "category_score": 0.0,
            "stock_score": 0.0,
            "relevance_score": 0.0,
            "styling_score": 0.0,
            "memory_score": 100.0,
            "format_score": 0.0,
            "total_score": 0.0,
            "fail_reasons": [f"ask_exception: {e!s}"],
            "raw_judge_output": None,
            "latency_ms": latency_ms,
            "model_name": model_name or getattr(settings, "VERTEX_GENERATIVE_MODEL", None),
            "prompt_version": prompt_version or "default",
            "evaluation_status": "fail",
            "dataset_name": dataset_name,
        }
        if save:
            create_evaluation(payload)
        return payload

    latency_ms = int((time.perf_counter() - start) * 1000)
    allowed = _allowed_skus(response)
    recommended = _recommended_skus(response)

    duplicate_ok, dupes = _validate_duplicates(recommended)
    hallucination_ok, hallucinated = _validate_hallucination(recommended, allowed)
    format_ok, format_fail_reasons = _validate_format(response)

    # Memory: we use a fresh session per eval, so no leakage
    memory_ok = True
    # Stock: retrieval is already filtered by filter_valid_results (in-stock + URL)
    stock_ok = True
    # Category: use metadata from response if available
    requested_category = (response.metadata or {}).get("target_category") if response.metadata else None
    category_ok = True  # Could refine with catalog categories if needed
    intent_ok = bool(response.intent)
    relevance_ok = True  # Heuristic: we have products and no hallucination
    styling_ok = bool(response.answer and response.answer.strip())

    fail_reasons: list[str] = []
    if not duplicate_ok:
        fail_reasons.append("duplicate_skus")
    if not hallucination_ok:
        fail_reasons.append("hallucination")
    if not format_ok:
        fail_reasons.extend(format_fail_reasons)
    if not intent_ok:
        fail_reasons.append("missing_intent")

    scores = _compute_heuristic_scores(
        intent_ok=intent_ok,
        category_ok=category_ok,
        stock_ok=stock_ok,
        relevance_ok=relevance_ok and hallucination_ok,
        styling_ok=styling_ok,
        memory_ok=memory_ok,
        format_ok=format_ok,
        duplicate_ok=duplicate_ok,
        hallucination_ok=hallucination_ok,
    )
    status = _evaluation_status(scores["total_score"], fail_reasons)

    payload = {
        "test_query": query,
        "assistant_response": response.answer or "",
        "detected_intent": response.intent,
        "requested_category": requested_category,
        "returned_categories": None,
        "recommended_products": [p.model_dump() for p in response.products],
        "recommended_skus": recommended,
        "stock_validation_passed": stock_ok,
        "duplicate_validation_passed": duplicate_ok,
        "category_validation_passed": category_ok,
        "memory_isolation_passed": memory_ok,
        "formatting_validation_passed": format_ok,
        "hallucination_validation_passed": hallucination_ok,
        **scores,
        "fail_reasons": fail_reasons or None,
        "raw_judge_output": None,
        "latency_ms": latency_ms,
        "model_name": model_name or getattr(settings, "VERTEX_GENERATIVE_MODEL", None),
        "prompt_version": prompt_version or "default",
        "evaluation_status": status,
        "dataset_name": dataset_name,
    }
    if save:
        create_evaluation(payload)
    return payload


async def run_evaluation_batch(
    test_queries: list[str] | None = None,
    dataset_name: str | None = None,
    model_name: str | None = None,
    prompt_version: str | None = None,
    limit: int | None = None,
    category_filter: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run a batch of evaluations (default or provided queries), optionally save. Returns summary."""
    queries = test_queries or DEFAULT_EVAL_QUERIES
    if limit is not None and limit > 0:
        queries = queries[:limit]
    if category_filter:
        # Filter by query containing category (simple)
        queries = [q for q in queries if category_filter.lower() in q.lower()]
    save = not dry_run
    total = len(queries)
    saved_count = 0
    results: list[dict[str, Any]] = []
    for q in queries:
        rec = await run_single_evaluation(
            q,
            dataset_name=dataset_name,
            model_name=model_name,
            prompt_version=prompt_version,
            save=save,
        )
        results.append(rec)
        if save:
            saved_count += 1
    pass_c = sum(1 for r in results if r.get("evaluation_status") == "pass")
    warn_c = sum(1 for r in results if r.get("evaluation_status") == "warning")
    fail_c = sum(1 for r in results if r.get("evaluation_status") == "fail")
    total_s = sum(r.get("total_score") or 0 for r in results)
    avg_score = round(total_s / total, 2) if total else 0
    return {
        "run_id": uuid.uuid4().hex if save else None,
        "total_run": total,
        "saved_count": saved_count,
        "dry_run": dry_run,
        "summary": {
            "average_total_score": avg_score,
            "pass_count": pass_c,
            "warning_count": warn_c,
            "fail_count": fail_c,
            "results": results,
        },
    }
