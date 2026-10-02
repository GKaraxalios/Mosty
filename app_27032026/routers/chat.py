from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.rate_limit import limiter, get_ai_limit
from app.models.schemas import (
    APIResponse,
    AttachmentUploadResponse,
    ChatCompareRequest,
    ChatCompareResponse,
    ChatRecommendRequest,
    ChatRecommendResponse,
    ChatAskErrorResponse,
    ChatRequest,
    ChatResponse,
    ChatShareCreateRequest,
    ChatShareStatusResponse,
    ChatSessionListResponse,
    ChatSessionResponse,
    ConversationListResponse,
    ConversationMessageItem,
    ConversationMessagesResponse,
    ConversationSummaryItem,
    RAGSearchResponse,
)
from app.services.attachment_service import get_attachment_service
from app.services.auth_service import verify_api_key
from app.services.chat_service import ChatService
from app.services.chat_share_service import ChatShareService
from app.services.memory_service import get_memory_service

logger = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(verify_api_key)])


# ── Session management ───────────────────────────────────────────────────────


@router.post("/session", response_model=ChatSessionResponse)
async def create_session(language: str = "auto") -> ChatSessionResponse:
    memory = get_memory_service()
    session = memory.create_session(language)
    return ChatSessionResponse(session=session)


@router.get("/session/{session_id}", response_model=ChatSessionResponse)
async def get_session(session_id: str) -> ChatSessionResponse:
    memory = get_memory_service()
    session = memory.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return ChatSessionResponse(session=session)


@router.delete("/session/{session_id}", response_model=APIResponse)
async def delete_session(session_id: str) -> APIResponse:
    """Delete a chat session and all its messages (idempotent: 200 even if already deleted)."""
    if not session_id or len(session_id) > 64:
        raise HTTPException(status_code=400, detail="Invalid session_id")
    memory = get_memory_service()
    memory.delete_session(session_id)
    return APIResponse(success=True)


@router.get("/sessions", response_model=ChatSessionListResponse)
async def list_sessions(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> ChatSessionListResponse:
    memory = get_memory_service()
    sessions = memory.list_sessions(limit=limit, offset=offset)
    return ChatSessionListResponse(sessions=sessions)


@router.delete("/session/{session_id}/history", response_model=APIResponse)
async def clear_session_history(session_id: str) -> APIResponse:
    memory = get_memory_service()
    cleared = memory.clear_session_history(session_id)
    if not cleared:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return APIResponse(success=True)


@router.get("/session/{session_id}/share", response_model=ChatShareStatusResponse)
async def get_session_share_status(request: Request, session_id: str) -> ChatShareStatusResponse:
    if not session_id or len(session_id) > 64:
        raise HTTPException(status_code=400, detail="Invalid session_id")
    svc = ChatShareService()
    payload = svc.get_share_status(session_id=session_id, base_url=str(request.base_url))
    return ChatShareStatusResponse(**payload)


@router.post("/session/{session_id}/share", response_model=ChatShareStatusResponse)
async def create_session_share(
    request: Request,
    session_id: str,
    body: ChatShareCreateRequest | None = None,
) -> ChatShareStatusResponse:
    if not session_id or len(session_id) > 64:
        raise HTTPException(status_code=400, detail="Invalid session_id")
    svc = ChatShareService()
    try:
        payload = svc.create_share(
            session_id=session_id,
            base_url=str(request.base_url),
            expires_in_hours=body.expires_in_hours if body else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ChatShareStatusResponse(**payload)


@router.delete("/session/{session_id}/share", response_model=ChatShareStatusResponse)
async def revoke_session_share(session_id: str) -> ChatShareStatusResponse:
    if not session_id or len(session_id) > 64:
        raise HTTPException(status_code=400, detail="Invalid session_id")
    svc = ChatShareService()
    payload = svc.revoke_share(session_id=session_id)
    return ChatShareStatusResponse(**payload)


# ── Chat endpoints ───────────────────────────────────────────────────────────


@router.post(
    "/ask",
    response_model=ChatResponse,
    responses={
        502: {"model": ChatAskErrorResponse, "description": "Upstream or runtime failure"},
        503: {"model": ChatAskErrorResponse, "description": "Unexpected chat failure"},
        504: {"model": ChatAskErrorResponse, "description": "Ask timed out"},
    },
)
@limiter.limit(get_ai_limit)
async def chat_ask(request: Request, body: ChatRequest) -> ChatResponse | JSONResponse:
    settings = get_settings()
    t0 = time.perf_counter()
    svc = ChatService()
    timeout = settings.CHAT_ASK_TIMEOUT_SECONDS
    try:
        if timeout and timeout > 0:
            resp = await asyncio.wait_for(svc.ask(body), timeout=timeout)
        else:
            resp = await svc.ask(body)
        dur = time.perf_counter() - t0
        intent = resp.intent or "-"
        logger.info(
            "[chat] /ask → intent=%s, duration=%.3fs, status=200",
            intent,
            dur,
        )
        return resp
    except asyncio.TimeoutError:
        dur = time.perf_counter() - t0
        logger.warning(
            "[chat] /ask → intent=-, duration=%.3fs, status=504 (timeout)",
            dur,
        )
        return JSONResponse(
            status_code=504,
            content=ChatAskErrorResponse(
                message="Request timed out. Please try again with a shorter question."
            ).model_dump(),
        )
    except RuntimeError as exc:
        dur = time.perf_counter() - t0
        logger.exception(
            "[chat] /ask → intent=-, duration=%.3fs, status=502",
            dur,
        )
        return JSONResponse(
            status_code=502,
            content=ChatAskErrorResponse(message=str(exc)).model_dump(),
        )
    except Exception as exc:
        dur = time.perf_counter() - t0
        logger.exception(
            "[chat] /ask → intent=-, duration=%.3fs, status=503",
            dur,
        )
        return JSONResponse(
            status_code=503,
            content=ChatAskErrorResponse(
                message=f"Chat ask failed: {exc}",
            ).model_dump(),
        )


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, description="Offset as string for pagination"),
) -> ConversationListResponse:
    memory = get_memory_service()
    offset = int(cursor) if cursor and str(cursor).isdigit() else 0
    sessions = memory.list_sessions(limit=limit, offset=offset)
    items: list[ConversationSummaryItem] = []
    for s in sessions:
        title = ((s.last_query or "").strip()[:60] or "Conversation")
        items.append(
            ConversationSummaryItem(
                id=s.session_id,
                title=title,
                created_at=s.created_at or "",
                updated_at=s.updated_at or "",
                last_query=s.last_query,
                message_count=len(s.conversation_history),
            )
        )
    next_cursor: str | None = None
    if len(sessions) == limit:
        next_cursor = str(offset + limit)
    return ConversationListResponse(items=items, next_cursor=next_cursor)


@router.get("/conversations/{session_id}/messages", response_model=ConversationMessagesResponse)
async def get_conversation_messages(
    session_id: str,
    limit: int = Query(50, ge=1, le=200),
    cursor: int = Query(0, ge=0),
) -> ConversationMessagesResponse:
    memory = get_memory_service()
    sess = memory.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    hist = sess.conversation_history
    chunk = hist[cursor : cursor + limit]
    messages = [
        ConversationMessageItem(role=t.role, content=t.content, timestamp=t.timestamp)
        for t in chunk
    ]
    next_cursor: int | None = None
    if cursor + len(chunk) < len(hist):
        next_cursor = cursor + len(chunk)
    return ConversationMessagesResponse(
        session_id=session_id,
        messages=messages,
        next_cursor=str(next_cursor) if next_cursor is not None else None,
    )


@router.post("/attachments", response_model=AttachmentUploadResponse)
async def upload_chat_attachment(
    file: UploadFile = File(...),
) -> AttachmentUploadResponse:
    data = await file.read()
    mime = file.content_type or "application/octet-stream"
    filename = file.filename or "upload"
    try:
        aid = get_attachment_service().save(data, mime, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AttachmentUploadResponse(
        attachment_id=aid,
        filename=filename,
        mime=mime,
        size_bytes=len(data),
    )


@router.get("/attachments/{attachment_id}", include_in_schema=False)
async def download_chat_attachment(attachment_id: str) -> Response:
    ent = get_attachment_service().get(attachment_id)
    if not ent:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return Response(content=ent.data, media_type=ent.mime)


@router.post("/search", response_model=RAGSearchResponse)
@limiter.limit(get_ai_limit)
async def chat_search(request: Request, body: ChatRequest) -> RAGSearchResponse:
    try:
        svc = ChatService()
        results = await svc.search(body)
        return RAGSearchResponse(query=body.query, results=results)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Chat search failed: {exc}")


@router.post("/recommend", response_model=ChatRecommendResponse)
@limiter.limit(get_ai_limit)
async def chat_recommend(request: Request, body: ChatRecommendRequest) -> ChatRecommendResponse:
    try:
        svc = ChatService()
        return await svc.recommend(body)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Chat recommend failed: {exc}")


@router.post("/compare", response_model=ChatCompareResponse)
@limiter.limit(get_ai_limit)
async def chat_compare(request: Request, body: ChatCompareRequest) -> ChatCompareResponse:
    try:
        svc = ChatService()
        return await svc.compare(body)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Chat compare failed: {exc}")
