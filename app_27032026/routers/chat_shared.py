from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.schemas import ChatRequest, ChatResponse, SharedChatAskRequest, SharedConversationResponse
from app.services.chat_service import ChatService
from app.services.chat_share_service import ChatShareService

router = APIRouter(prefix="/chat", tags=["chat-shared"])


@router.get("/shared/{token}", response_model=SharedConversationResponse)
async def get_shared_conversation(token: str) -> SharedConversationResponse:
    if not token or len(token) < 20:
        raise HTTPException(status_code=404, detail="Shared conversation not found")
    svc = ChatShareService()
    payload = svc.get_shared_conversation(token=token)
    if payload is None:
        raise HTTPException(status_code=404, detail="Shared conversation not found")
    return SharedConversationResponse(conversation=payload)


@router.post("/shared/{token}/ask", response_model=ChatResponse)
async def ask_shared_conversation(token: str, body: SharedChatAskRequest) -> ChatResponse:
    if not token or len(token) < 20:
        raise HTTPException(status_code=404, detail="Shared conversation not found")
    svc = ChatShareService()
    session_id = svc.resolve_shared_session_id(token=token)
    if not session_id:
        raise HTTPException(status_code=404, detail="Shared conversation not found")
    if not body.query or not body.query.strip():
        raise HTTPException(status_code=400, detail="Query is required")
    req = ChatRequest(
        session_id=session_id,
        query=body.query,
        top_k=body.top_k,
        temperature=body.temperature,
        filters=body.filters,
        language=body.language,
        location_city=body.location_city,
        location_lat=body.location_lat,
        location_lon=body.location_lon,
    )
    return await ChatService().ask(req)


