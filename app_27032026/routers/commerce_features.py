"""Commerce features router.

Endpoints for: user profile (persistent), wishlist, saved looks, product feedback,
analytics, contradiction detection, complete-the-look, structured compare,
alternatives, and alerts.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.models.schemas import (
    AlertListResponse,
    AlternativesResponse,
    AnalyticsSummaryResponse,
    CompleteLookResponse,
    ContradictionCheckResponse,
    HybridSearchFilters,
    StructuredCompareResponse,
    UserProfileResponse,
)
from app.services.auth_service import verify_api_key
from app.services.commerce_features_service import CommerceFeaturesService
from app.services.memory_service import get_memory_service

router = APIRouter(
    prefix="/commerce",
    tags=["commerce-features"],
    dependencies=[Depends(verify_api_key)],
)

_svc: CommerceFeaturesService | None = None
_memory_inst = None


def _get_svc() -> CommerceFeaturesService:
    global _svc
    if _svc is None:
        _svc = CommerceFeaturesService()
    return _svc


def _get_memory():
    global _memory_inst
    if _memory_inst is None:
        _memory_inst = get_memory_service()
    return _memory_inst


# ── Request Models ───────────────────────────────────────────────────────────


class WishlistAddRequest(BaseModel):
    session_id: str
    sku: str
    note: str | None = None


class WishlistRemoveRequest(BaseModel):
    session_id: str
    sku: str


class SavedLookCreateRequest(BaseModel):
    session_id: str
    title: str
    skus: list[str] = Field(default_factory=list)
    occasion: str | None = None
    note: str | None = None


class FeedbackRequest(BaseModel):
    session_id: str
    sku: str
    action: str
    reason: str | None = None


class PreferencesPatchRequest(BaseModel):
    budget_min: float | None = None
    budget_max: float | None = None
    preferred_colors: list[str] | None = None
    preferred_categories: list[str] | None = None
    preferred_styles: list[str] | None = None
    occasion: str | None = None
    language: str | None = None
    rejected_skus: list[str] | None = None
    liked_skus: list[str] | None = None
    preferred_sizes: list[str] | None = None
    preferred_brands: list[str] | None = None
    fit_preference: str | None = None


class CompleteLookRequest(BaseModel):
    sku: str
    budget_max: float | None = None
    occasion: str | None = None
    style: str | None = None
    top_k_per_group: int = 3


class StructuredCompareRequest(BaseModel):
    skus: list[str] = Field(..., min_length=2, max_length=10)


class AlternativesRequest(BaseModel):
    sku: str
    types: list[str] | None = None
    top_k: int = 4


class ContradictionCheckRequest(BaseModel):
    query: str | None = None
    filters: HybridSearchFilters | None = None


class AlertCreateRequest(BaseModel):
    session_id: str
    alert_type: str
    config: dict[str, Any] | None = None


class AlertDeleteRequest(BaseModel):
    session_id: str


# ── Profile (persistent) ────────────────────────────────────────────────────


@router.get("/profile/{session_id}", response_model=UserProfileResponse)
def get_profile(session_id: str) -> UserProfileResponse:
    from app.services.user_profile_service import UserProfileService
    profile_svc = UserProfileService()
    persistent = profile_svc.get_profile(session_id)

    session = _get_memory().get_session(session_id)
    if not session and not persistent:
        raise HTTPException(status_code=404, detail="Session not found")

    if persistent:
        profile_svc.merge_with_session(session_id)
        return UserProfileResponse(session_id=session_id, profile=persistent)

    prefs = session.user_preferences.model_dump() if session else {}
    return UserProfileResponse(session_id=session_id, profile=prefs)


@router.patch("/profile/{session_id}", response_model=UserProfileResponse)
def patch_profile(session_id: str, body: PreferencesPatchRequest) -> UserProfileResponse:
    from app.services.user_profile_service import UserProfileService
    profile_svc = UserProfileService()

    session = _get_memory().get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    payload = body.model_dump(exclude_none=True)
    if payload:
        _get_memory().update_preferences(session_id, **payload)
        profile_svc.upsert_profile(session_id, payload)
        _get_svc().track_event(session_id, "profile_update", {"fields": list(payload.keys())})

    updated = profile_svc.get_profile(session_id)
    if not updated:
        updated_session = _get_memory().get_session(session_id)
        updated = updated_session.user_preferences.model_dump() if updated_session else payload
    return UserProfileResponse(session_id=session_id, profile=updated)


# ── Wishlist ─────────────────────────────────────────────────────────────────


@router.post("/wishlist/add")
def add_to_wishlist(body: WishlistAddRequest) -> dict[str, Any]:
    session = _get_memory().get_session(body.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    result = _get_svc().add_to_wishlist(body.session_id, body.sku, body.note)
    _get_svc().track_event(body.session_id, "wishlist_add", {"sku": body.sku})
    return {"success": True, **result}


@router.post("/wishlist/remove")
def remove_from_wishlist(body: WishlistRemoveRequest) -> dict[str, Any]:
    removed = _get_svc().remove_from_wishlist(body.session_id, body.sku)
    if removed:
        _get_svc().track_event(body.session_id, "wishlist_remove", {"sku": body.sku})
    return {"success": True, "removed": removed, "sku": body.sku}


@router.get("/wishlist/{session_id}")
def get_wishlist(session_id: str) -> dict[str, Any]:
    return {"success": True, "session_id": session_id, "items": _get_svc().list_wishlist(session_id)}


# ── Saved Looks ──────────────────────────────────────────────────────────────


@router.post("/looks/save")
def save_look(body: SavedLookCreateRequest) -> dict[str, Any]:
    if not body.skus:
        raise HTTPException(status_code=422, detail="At least one SKU is required")
    row = _get_svc().save_look(body.session_id, body.title, body.skus, body.occasion, body.note)
    _get_svc().track_event(
        body.session_id,
        "saved_look_create",
        {"look_id": row["id"], "sku_count": len(body.skus)},
    )
    return {"success": True, "look": row}


@router.get("/looks/{session_id}")
def list_looks(session_id: str) -> dict[str, Any]:
    return {"success": True, "session_id": session_id, "looks": _get_svc().list_saved_looks(session_id)}


@router.delete("/looks/{session_id}/{look_id}")
def delete_look(session_id: str, look_id: int) -> dict[str, Any]:
    deleted = _get_svc().delete_look(look_id, session_id)
    if deleted:
        _get_svc().track_event(session_id, "saved_look_delete", {"look_id": look_id})
    return {"success": True, "deleted": deleted, "look_id": look_id}


# ── Product Feedback ─────────────────────────────────────────────────────────


@router.post("/feedback")
def add_feedback(body: FeedbackRequest) -> dict[str, Any]:
    try:
        result = _get_svc().add_feedback(body.session_id, body.sku, body.action, body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    _get_svc().track_event(body.session_id, "product_feedback", {"sku": body.sku, "action": body.action})

    session = _get_memory().get_session(body.session_id)
    if session:
        prefs = session.user_preferences
        if body.action == "like":
            _get_memory().update_preferences(
                body.session_id,
                liked_skus=list(set((prefs.liked_skus or []) + [body.sku])),
            )
        elif body.action in {"dislike", "reject"}:
            _get_memory().update_preferences(
                body.session_id,
                rejected_skus=list(set((prefs.rejected_skus or []) + [body.sku])),
            )

        from app.services.user_profile_service import UserProfileService
        profile_svc = UserProfileService()
        if body.action == "like":
            profile_svc.upsert_profile(body.session_id, {"liked_skus": list(set((prefs.liked_skus or []) + [body.sku]))})
        elif body.action in {"dislike", "reject"}:
            profile_svc.upsert_profile(body.session_id, {"rejected_skus": list(set((prefs.rejected_skus or []) + [body.sku]))})

    return {"success": True, **result}


# ── Analytics ────────────────────────────────────────────────────────────────


@router.get("/analytics/summary", response_model=AnalyticsSummaryResponse)
def analytics_summary() -> AnalyticsSummaryResponse:
    data = _get_svc().analytics_summary()
    return AnalyticsSummaryResponse(success=True, **data)


# ── Contradiction Detection ──────────────────────────────────────────────────


@router.post("/filters/check-contradictions", response_model=ContradictionCheckResponse)
def check_contradictions(body: ContradictionCheckRequest) -> ContradictionCheckResponse:
    from app.services.contradiction_service import ContradictionService
    result = ContradictionService().check(filters=body.filters, query=body.query)
    if result["has_contradictions"]:
        _get_svc().track_event("system", "contradiction_detected", {
            "contradictions": result["contradictions"],
            "query": body.query,
        })
    return ContradictionCheckResponse(success=True, **result)


# ── Complete the Look ────────────────────────────────────────────────────────


@router.post("/complete-the-look", response_model=CompleteLookResponse)
async def complete_the_look(body: CompleteLookRequest) -> CompleteLookResponse:
    from app.services.complete_look_service import CompleteLookService
    svc = CompleteLookService()
    result = await svc.complete_the_look(
        sku=body.sku,
        budget_max=body.budget_max,
        occasion=body.occasion,
        style=body.style,
        top_k_per_group=body.top_k_per_group,
    )
    _get_svc().track_event("system", "complete_the_look_request", {"sku": body.sku})
    return CompleteLookResponse(success=True, **result)


# ── Structured Compare ───────────────────────────────────────────────────────


@router.post("/compare/structured", response_model=StructuredCompareResponse)
def structured_compare(body: StructuredCompareRequest) -> StructuredCompareResponse:
    from app.services.structured_compare_service import StructuredCompareService
    result = StructuredCompareService().compare(body.skus)
    _get_svc().track_event("system", "compare_request", {"skus": body.skus})
    return StructuredCompareResponse(success=True, **result)


# ── Alternatives ─────────────────────────────────────────────────────────────


@router.post("/alternatives", response_model=AlternativesResponse)
async def find_alternatives(body: AlternativesRequest) -> AlternativesResponse:
    from app.services.alternatives_service import AlternativesService
    result = await AlternativesService().find_alternatives(
        sku=body.sku, types=body.types, top_k=body.top_k,
    )
    _get_svc().track_event("system", "alternatives_request", {"sku": body.sku, "types": body.types})
    return AlternativesResponse(success=True, **result)


# ── Alerts ───────────────────────────────────────────────────────────────────


@router.post("/alerts")
def create_alert(body: AlertCreateRequest) -> dict[str, Any]:
    from app.services.alerts_service import AlertsService
    try:
        alert = AlertsService().create_alert(body.session_id, body.alert_type, body.config)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    _get_svc().track_event(body.session_id, "alert_create", {"alert_type": body.alert_type})
    return {"success": True, "alert": alert}


@router.get("/alerts/{session_id}", response_model=AlertListResponse)
def list_alerts(
    session_id: str,
    status: str | None = Query(None, description="Filter by status: active, triggered, dismissed"),
) -> AlertListResponse:
    from app.services.alerts_service import AlertsService
    alerts = AlertsService().list_alerts(session_id, status=status)
    return AlertListResponse(
        success=True,
        alerts=[
            {
                "id": a["id"],
                "session_id": a["session_id"],
                "alert_type": a["alert_type"],
                "status": a["status"],
                "config": a["config"],
                "created_at": a["created_at"],
                "triggered_at": a["triggered_at"],
            }
            for a in alerts
        ],
    )


@router.delete("/alerts/{session_id}/{alert_id}")
def delete_alert(session_id: str, alert_id: int) -> dict[str, Any]:
    from app.services.alerts_service import AlertsService
    deleted = AlertsService().delete_alert(alert_id, session_id)
    return {"success": True, "deleted": deleted, "alert_id": alert_id}


@router.post("/alerts/{session_id}/{alert_id}/dismiss")
def dismiss_alert(session_id: str, alert_id: int) -> dict[str, Any]:
    from app.services.alerts_service import AlertsService
    dismissed = AlertsService().dismiss_alert(alert_id, session_id)
    return {"success": True, "dismissed": dismissed, "alert_id": alert_id}
