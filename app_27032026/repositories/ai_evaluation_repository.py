"""Persistence for AI evaluation records (admin monitoring)."""

from __future__ import annotations

import json as _json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select, text

from app.db.base import create_tables, session_scope
from app.db.models import AIEvaluationModel
from app.models.schemas import AIEvaluationRecord

# Default benchmark test queries for evaluation runs
DEFAULT_EVAL_QUERIES = [
    "θέλω φορέματα για βάφτιση",
    "δείξε μου casual παπούτσια",
    "τι ταιριάζει με μαύρο φόρεμα",
    "ψάχνω τσάντα για γάμο",
    "θέλω κάτι versatile που να το ξαναφορέσω",
    "παπούτσια για γραφείο",
    "outfit για καλοκαιρινό brunch",
]


def _row_to_record(row: AIEvaluationModel) -> AIEvaluationRecord:
    return AIEvaluationRecord(
        id=row.id,
        created_at=row.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if row.created_at else "",
        test_query=row.test_query or "",
        assistant_response=row.assistant_response or "",
        detected_intent=row.detected_intent,
        requested_category=row.requested_category,
        returned_categories=row.returned_categories if isinstance(row.returned_categories, list) else None,
        recommended_products=row.recommended_products if isinstance(row.recommended_products, list) else None,
        recommended_skus=row.recommended_skus if isinstance(row.recommended_skus, list) else None,
        stock_validation_passed=bool(row.stock_validation_passed),
        duplicate_validation_passed=bool(row.duplicate_validation_passed),
        category_validation_passed=bool(row.category_validation_passed),
        memory_isolation_passed=bool(row.memory_isolation_passed),
        formatting_validation_passed=bool(row.formatting_validation_passed),
        hallucination_validation_passed=bool(row.hallucination_validation_passed),
        intent_score=float(row.intent_score) if row.intent_score is not None else None,
        category_score=float(row.category_score) if row.category_score is not None else None,
        stock_score=float(row.stock_score) if row.stock_score is not None else None,
        relevance_score=float(row.relevance_score) if row.relevance_score is not None else None,
        styling_score=float(row.styling_score) if row.styling_score is not None else None,
        memory_score=float(row.memory_score) if row.memory_score is not None else None,
        format_score=float(row.format_score) if row.format_score is not None else None,
        total_score=float(row.total_score) if row.total_score is not None else None,
        fail_reasons=row.fail_reasons if isinstance(row.fail_reasons, list) else None,
        raw_judge_output=row.raw_judge_output,
        latency_ms=row.latency_ms,
        model_name=row.model_name,
        prompt_version=row.prompt_version,
        evaluation_status=row.evaluation_status or "pass",
        dataset_name=row.dataset_name,
    )


def create_evaluation(data: dict[str, Any]) -> AIEvaluationRecord:
    with session_scope() as db:
        row = AIEvaluationModel(
            test_query=data["test_query"],
            assistant_response=data["assistant_response"],
            detected_intent=data.get("detected_intent"),
            requested_category=data.get("requested_category"),
            returned_categories=data.get("returned_categories"),
            recommended_products=data.get("recommended_products"),
            recommended_skus=data.get("recommended_skus"),
            stock_validation_passed=data.get("stock_validation_passed", True),
            duplicate_validation_passed=data.get("duplicate_validation_passed", True),
            category_validation_passed=data.get("category_validation_passed", True),
            memory_isolation_passed=data.get("memory_isolation_passed", True),
            formatting_validation_passed=data.get("formatting_validation_passed", True),
            hallucination_validation_passed=data.get("hallucination_validation_passed", True),
            intent_score=data.get("intent_score"),
            category_score=data.get("category_score"),
            stock_score=data.get("stock_score"),
            relevance_score=data.get("relevance_score"),
            styling_score=data.get("styling_score"),
            memory_score=data.get("memory_score"),
            format_score=data.get("format_score"),
            total_score=data.get("total_score"),
            fail_reasons=data.get("fail_reasons"),
            raw_judge_output=data.get("raw_judge_output"),
            latency_ms=data.get("latency_ms"),
            model_name=data.get("model_name"),
            prompt_version=data.get("prompt_version"),
            evaluation_status=data.get("evaluation_status", "pass"),
            dataset_name=data.get("dataset_name"),
        )
        db.add(row)
        db.flush()
        db.refresh(row)
        return _row_to_record(row)


def get_evaluation_by_id(eval_id: int) -> AIEvaluationRecord | None:
    with session_scope() as db:
        row = db.execute(select(AIEvaluationModel).where(AIEvaluationModel.id == eval_id)).scalars().one_or_none()
        return _row_to_record(row) if row else None


def _parse_date(s: str | None) -> datetime | None:
    if not s or not s.strip():
        return None
    s = s.strip().rstrip("Z")
    try:
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        pass
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except ValueError:
        return None


def list_evaluations(
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    min_score: float | None = None,
    max_score: float | None = None,
    category: str | None = None,
    query_search: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    model_name: str | None = None,
    prompt_version: str | None = None,
) -> tuple[list[AIEvaluationRecord], int]:
    dt_from = _parse_date(date_from)
    dt_to = _parse_date(date_to)
    with session_scope() as db:
        q = select(AIEvaluationModel)
        count_q = select(func.count()).select_from(AIEvaluationModel)
        if status:
            q = q.where(AIEvaluationModel.evaluation_status == status)
            count_q = count_q.where(AIEvaluationModel.evaluation_status == status)
        if min_score is not None:
            q = q.where(AIEvaluationModel.total_score >= min_score)
            count_q = count_q.where(AIEvaluationModel.total_score >= min_score)
        if max_score is not None:
            q = q.where(AIEvaluationModel.total_score <= max_score)
            count_q = count_q.where(AIEvaluationModel.total_score <= max_score)
        if category:
            q = q.where(AIEvaluationModel.requested_category == category)
            count_q = count_q.where(AIEvaluationModel.requested_category == category)
        if query_search:
            q = q.where(AIEvaluationModel.test_query.contains(query_search))
            count_q = count_q.where(AIEvaluationModel.test_query.contains(query_search))
        if dt_from:
            q = q.where(AIEvaluationModel.created_at >= dt_from)
            count_q = count_q.where(AIEvaluationModel.created_at >= dt_from)
        if dt_to:
            q = q.where(AIEvaluationModel.created_at <= dt_to)
            count_q = count_q.where(AIEvaluationModel.created_at <= dt_to)
        if model_name:
            q = q.where(AIEvaluationModel.model_name == model_name)
            count_q = count_q.where(AIEvaluationModel.model_name == model_name)
        if prompt_version:
            q = q.where(AIEvaluationModel.prompt_version == prompt_version)
            count_q = count_q.where(AIEvaluationModel.prompt_version == prompt_version)
        total = db.execute(count_q).scalar() or 0
        q = q.order_by(AIEvaluationModel.created_at.desc())
        q = q.offset((page - 1) * page_size).limit(page_size)
        rows = db.execute(q).scalars().all()
        return [_row_to_record(r) for r in rows], int(total)


def list_failures(
    max_total_score: float = 60.0,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[AIEvaluationRecord], int]:
    fail_condition = or_(
        AIEvaluationModel.evaluation_status == "fail",
        AIEvaluationModel.total_score < max_total_score,
    )
    with session_scope() as db:
        q = select(AIEvaluationModel).where(fail_condition).order_by(AIEvaluationModel.created_at.desc())
        count_q = select(func.count()).select_from(AIEvaluationModel).where(fail_condition)
        total = db.execute(count_q).scalar() or 0
        rows = db.execute(q.offset((page - 1) * page_size).limit(page_size)).scalars().all()
        return [_row_to_record(r) for r in rows], int(total)


def get_summary_stats() -> dict[str, Any]:
    with session_scope() as db:
        total = db.execute(select(func.count(AIEvaluationModel.id))).scalar() or 0
        if total == 0:
            return {
                "total_tests": 0,
                "average_total_score": None,
                "average_intent_score": None,
                "average_category_score": None,
                "average_stock_score": None,
                "average_relevance_score": None,
                "average_styling_score": None,
                "average_memory_score": None,
                "average_format_score": None,
                "pass_count": 0,
                "warning_count": 0,
                "fail_count": 0,
                "fail_percentage": None,
                "most_common_fail_reasons": [],
                "most_common_wrong_categories": [],
                "most_common_out_of_stock": [],
                "most_common_hallucinations": [],
            }
        avg = db.execute(
            select(
                func.avg(AIEvaluationModel.total_score).label("total"),
                func.avg(AIEvaluationModel.intent_score).label("intent"),
                func.avg(AIEvaluationModel.category_score).label("category"),
                func.avg(AIEvaluationModel.stock_score).label("stock"),
                func.avg(AIEvaluationModel.relevance_score).label("relevance"),
                func.avg(AIEvaluationModel.styling_score).label("styling"),
                func.avg(AIEvaluationModel.memory_score).label("memory"),
                func.avg(AIEvaluationModel.format_score).label("format"),
            )
        ).one()
        status_counts = db.execute(
            select(AIEvaluationModel.evaluation_status, func.count(AIEvaluationModel.id))
            .group_by(AIEvaluationModel.evaluation_status)
        ).all()
        pass_c = next((c for s, c in status_counts if s == "pass"), 0)
        warn_c = next((c for s, c in status_counts if s == "warning"), 0)
        fail_c = next((c for s, c in status_counts if s == "fail"), 0)
        fail_pct = (fail_c / total * 100) if total else None
        fail_rows = db.execute(
            select(AIEvaluationModel.fail_reasons)
            .where(AIEvaluationModel.fail_reasons.is_not(None))
        ).scalars().all()
        reason_counter: Counter[str] = Counter()
        for fr in fail_rows:
            reasons = fr if isinstance(fr, list) else []
            for r in reasons:
                if isinstance(r, str) and r.strip():
                    reason_counter[r.strip()] += 1
        common_reasons = [{"reason": k, "count": v} for k, v in reason_counter.most_common(10)]

        avg_latency_val = db.execute(
            select(func.avg(AIEvaluationModel.latency_ms))
        ).scalar()

        return {
            "total_tests": total,
            "average_total_score": round(float(avg.total), 2) if avg.total is not None else None,
            "average_intent_score": round(float(avg.intent), 2) if avg.intent is not None else None,
            "average_category_score": round(float(avg.category), 2) if avg.category is not None else None,
            "average_stock_score": round(float(avg.stock), 2) if avg.stock is not None else None,
            "average_relevance_score": round(float(avg.relevance), 2) if avg.relevance is not None else None,
            "average_styling_score": round(float(avg.styling), 2) if avg.styling is not None else None,
            "average_memory_score": round(float(avg.memory), 2) if avg.memory is not None else None,
            "average_format_score": round(float(avg.format), 2) if avg.format is not None else None,
            "average_latency_ms": round(float(avg_latency_val)) if avg_latency_val is not None else None,
            "pass_count": pass_c,
            "warning_count": warn_c,
            "fail_count": fail_c,
            "fail_percentage": round(fail_pct, 2) if fail_pct is not None else None,
            "most_common_fail_reasons": common_reasons,
            "most_common_wrong_categories": [],
            "most_common_out_of_stock": [],
            "most_common_hallucinations": [],
        }


def get_all_for_export(
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 10000,
) -> list[AIEvaluationRecord]:
    dt_from = _parse_date(date_from)
    dt_to = _parse_date(date_to)
    with session_scope() as db:
        q = select(AIEvaluationModel).order_by(AIEvaluationModel.created_at.desc()).limit(limit)
        if status:
            q = q.where(AIEvaluationModel.evaluation_status == status)
        if dt_from:
            q = q.where(AIEvaluationModel.created_at >= dt_from)
        if dt_to:
            q = q.where(AIEvaluationModel.created_at <= dt_to)
        rows = db.execute(q).scalars().all()
        return [_row_to_record(r) for r in rows]


def get_chart_data(limit: int = 200) -> dict[str, Any]:
    """Return data for dashboard charts: score trend, status distribution, latency trend."""
    with session_scope() as db:
        rows = db.execute(
            select(
                AIEvaluationModel.id,
                AIEvaluationModel.created_at,
                AIEvaluationModel.total_score,
                AIEvaluationModel.evaluation_status,
                AIEvaluationModel.latency_ms,
            )
            .order_by(AIEvaluationModel.created_at.asc())
            .limit(limit)
        ).all()
        labels: list[str] = []
        scores: list[float | None] = []
        latencies: list[int | None] = []
        status_map: Counter[str] = Counter()
        for row in rows:
            labels.append(row.created_at.strftime("%Y-%m-%d %H:%M") if row.created_at else "")
            scores.append(float(row.total_score) if row.total_score is not None else None)
            latencies.append(row.latency_ms)
            status_map[row.evaluation_status or "pass"] += 1
        return {
            "labels": labels,
            "scores": scores,
            "latencies": latencies,
            "status_distribution": dict(status_map),
        }


def get_distinct_filters() -> dict[str, list[str]]:
    """Return distinct values for filter dropdowns."""
    with session_scope() as db:
        models = db.execute(
            select(AIEvaluationModel.model_name)
            .where(AIEvaluationModel.model_name.is_not(None))
            .distinct()
        ).scalars().all()
        versions = db.execute(
            select(AIEvaluationModel.prompt_version)
            .where(AIEvaluationModel.prompt_version.is_not(None))
            .distinct()
        ).scalars().all()
        categories = db.execute(
            select(AIEvaluationModel.requested_category)
            .where(AIEvaluationModel.requested_category.is_not(None))
            .distinct()
        ).scalars().all()
        datasets = db.execute(
            select(AIEvaluationModel.dataset_name)
            .where(AIEvaluationModel.dataset_name.is_not(None))
            .distinct()
        ).scalars().all()
        return {
            "model_names": sorted(set(str(m) for m in models if m)),
            "prompt_versions": sorted(set(str(v) for v in versions if v)),
            "categories": sorted(set(str(c) for c in categories if c)),
            "datasets": sorted(set(str(d) for d in datasets if d)),
        }


def get_worst_queries(limit: int = 5) -> list[AIEvaluationRecord]:
    with session_scope() as db:
        rows = db.execute(
            select(AIEvaluationModel)
            .where(AIEvaluationModel.total_score.is_not(None))
            .order_by(AIEvaluationModel.total_score.asc())
            .limit(limit)
        ).scalars().all()
        return [_row_to_record(r) for r in rows]


def get_best_queries(limit: int = 5) -> list[AIEvaluationRecord]:
    with session_scope() as db:
        rows = db.execute(
            select(AIEvaluationModel)
            .where(AIEvaluationModel.total_score.is_not(None))
            .order_by(AIEvaluationModel.total_score.desc())
            .limit(limit)
        ).scalars().all()
        return [_row_to_record(r) for r in rows]


def ensure_table() -> None:
    try:
        create_tables()
    except Exception:
        pass
