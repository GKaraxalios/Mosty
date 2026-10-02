"""Outfit Builder API: slot search, suggestions, save/load, public share view."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.models.schemas import (
    OutfitBuilderContext,
    OutfitPublicViewResponse,
    OutfitRecordResponse,
    OutfitSaveRequest,
    OutfitSlotSearchRequest,
    OutfitSlotSearchResponse,
    OutfitSlotsState,
    OutfitSuggestionsRequest,
    OutfitSuggestionsResponse,
)
from app.services.auth_service import verify_api_key
from app.services.outfit_builder_service import (
    OutfitBuilderService,
    payload_to_slots,
    slots_to_payload,
)
from app.services import outfit_store_service as store

router = APIRouter(prefix="/outfit-builder", tags=["outfit-builder"])

STATIC_DIR = Path(__file__).resolve().parents[2] / "static"

_svc: OutfitBuilderService | None = None


def _get_svc() -> OutfitBuilderService:
    global _svc
    if _svc is None:
        _svc = OutfitBuilderService()
    return _svc


@router.get("/ui-config")
async def outfit_builder_ui_config(
    variant: str = Query("production", description="Schema: outfit-builder.{variant}.json"),
) -> JSONResponse:
    """Same JSON as static/schemas/outfit-builder.{variant}.json (fallback if /static fails)."""
    safe = variant.replace("/", "").replace("..", "") or "production"
    path = STATIC_DIR / "schemas" / f"outfit-builder.{safe}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Unknown outfit-builder variant: {safe}")
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@router.get("/bootstrap")
async def outfit_builder_bootstrap(request: Request) -> JSONResponse:
    """Public env + endpoint paths for the Outfit Builder page (no API key)."""
    settings = get_settings()
    base = str(request.base_url).rstrip("/")
    p = settings.API_V1_PREFIX
    api = f"{base}{p}"
    return JSONResponse(
        {
            "success": True,
            "env": {
                "origin": base,
                "apiBaseUrl": f"{api}/",
                "staticBaseUrl": f"{base}/static/",
            },
            "endpoints": {
                "searchSlot": f"{api}/outfit-builder/search-slot",
                "suggestions": f"{api}/outfit-builder/suggestions",
                "randomize": f"{api}/outfit-builder/randomize",
                "saveOutfit": f"{api}/outfit-builder/outfits",
                "getOutfit": f"{api}/outfit-builder/outfits/{{id}}",
                "sharePublic": f"{api}/outfit-builder/share/{{token}}",
                "uiConfig": f"{api}/outfit-builder/ui-config",
            },
        }
    )


@router.post(
    "/search-slot",
    response_model=OutfitSlotSearchResponse,
    dependencies=[Depends(verify_api_key)],
)
async def search_slot(body: OutfitSlotSearchRequest) -> OutfitSlotSearchResponse:
    svc = _get_svc()
    results = await svc.search_slot(
        body.slot,
        body.context,
        query_override=body.query_override,
        exclude_skus=body.exclude_skus,
        top_k=body.top_k,
    )
    return OutfitSlotSearchResponse(slot=body.slot, context=body.context, results=results)


@router.post(
    "/suggestions",
    response_model=OutfitSuggestionsResponse,
    dependencies=[Depends(verify_api_key)],
)
async def suggestions(body: OutfitSuggestionsRequest) -> OutfitSuggestionsResponse:
    svc = _get_svc()
    sug, warnings, score = await svc.build_suggestions(body.slots, body.context)
    return OutfitSuggestionsResponse(
        slots=body.slots,
        context=body.context,
        suggestions=sug,
        mismatch_warnings=warnings,
        compatibility_score=score,
    )


@router.post(
    "/randomize",
    response_model=OutfitSuggestionsResponse,
    dependencies=[Depends(verify_api_key)],
)
async def randomize(body: OutfitSuggestionsRequest) -> OutfitSuggestionsResponse:
    svc = _get_svc()
    new_slots = await svc.randomize_outfit(body.context)
    sug, warnings, score = await svc.build_suggestions(new_slots, body.context)
    return OutfitSuggestionsResponse(
        slots=new_slots,
        context=body.context,
        suggestions=sug,
        mismatch_warnings=warnings,
        compatibility_score=score,
    )


@router.post(
    "/outfits",
    response_model=OutfitRecordResponse,
    dependencies=[Depends(verify_api_key)],
)
async def save_outfit(request: Request, body: OutfitSaveRequest) -> OutfitRecordResponse:
    payload = {
        "title": body.title,
        "slots": slots_to_payload(body.slots),
        "context": body.context.model_dump(exclude_none=True),
    }
    rec = store.save_outfit(payload)
    base = str(request.base_url).rstrip("/")
    token = rec["share_token"]
    share_url = f"{base}/outfit-builder?share={token}"
    return OutfitRecordResponse(
        success=True,
        outfit_id=rec["outfit_id"],
        share_url=share_url,
        title=rec["title"],
        slots=body.slots,
        context=body.context,
        created_at=rec["created_at"],
        share_token=token,
    )


@router.get(
    "/outfits/{outfit_id}",
    response_model=OutfitRecordResponse,
    dependencies=[Depends(verify_api_key)],
)
async def get_outfit(outfit_id: str) -> OutfitRecordResponse:
    rec = store.get_outfit(outfit_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Outfit not found")
    slots = payload_to_slots(rec.get("slots") or {})
    ctx = OutfitBuilderContext.model_validate(rec.get("context") or {})
    return OutfitRecordResponse(
        success=True,
        outfit_id=rec["outfit_id"],
        share_url=None,
        title=rec.get("title") or "",
        slots=slots,
        context=ctx,
        created_at=rec.get("created_at") or "",
        share_token=rec.get("share_token"),
    )


@router.get("/share/{token}", response_model=OutfitPublicViewResponse)
async def get_public_outfit(token: str) -> OutfitPublicViewResponse:
    """Read-only outfit by share token (no API key)."""
    rec = store.get_by_share_token(token)
    if not rec:
        raise HTTPException(status_code=404, detail="Outfit not found or link expired")
    slots = payload_to_slots(rec.get("slots") or {})
    ctx = OutfitBuilderContext.model_validate(rec.get("context") or {})
    return OutfitPublicViewResponse(
        success=True,
        outfit_id=rec["outfit_id"],
        title=rec.get("title") or "",
        slots=slots,
        context=ctx,
        created_at=rec.get("created_at") or "",
    )
