from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from app.models.schemas import (
    APIResponse,
    ChatCompareRequest,
    ChatCompareResponse,
    ChatRecommendRequest,
    ChatRecommendResponse,
    ChatRequest,
    ChatResponse,
    ChatSessionListResponse,
    ChatSessionResponse,
    ConversationListResponse,
    ConversationMessageItem,
    ConversationMessagesResponse,
    ConversationSummaryItem,
    RAGSearchResponse,
)
from app.core.log_context import get_request_id
from app.core.logging import get_logger
from app.services.auth_service import verify_api_key
from app.services.chat_service import ChatService
from app.services.memory_service import get_memory_service

logger = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(verify_api_key)])


_SAFE_MESSAGES = {
    "upstream_unavailable": {
        "el": "Η υπηρεσία αναζήτησης προϊόντων αντιμετωπίζει προσωρινό τεχνικό πρόβλημα. Παρακαλώ δοκιμάστε ξανά.",
        "en": "The product search service is having a temporary problem. Please try again.",
    },
    "catalog_unavailable": {
        "el": "Δεν ήταν δυνατή η ανάκτηση στοιχείων προϊόντων αυτή τη στιγμή. Παρακαλώ δοκιμάστε ξανά.",
        "en": "Product information could not be retrieved right now. Please try again.",
    },
    "internal_error": {
        "el": "Παρουσιάστηκε απρόσμενο σφάλμα. Παρακαλώ δοκιμάστε ξανά.",
        "en": "Something went wrong. Please try again.",
    },
}


def _classify_chat_error(exc: Exception) -> tuple[int, str, int | None]:
    """Map an exception to (http_status, error_code, retry_after_ms)."""
    from google.api_core import exceptions as gexc

    if isinstance(exc, RuntimeError):
        # Raised by the Magento GraphQL client on GraphQL-level errors.
        return 502, "catalog_unavailable", None
    if isinstance(exc, (gexc.ResourceExhausted, gexc.TooManyRequests)):
        return 503, "upstream_unavailable", 5000
    if isinstance(exc, (gexc.ServiceUnavailable, gexc.DeadlineExceeded, gexc.ServerError, TimeoutError)):
        return 503, "upstream_unavailable", 2000
    if isinstance(exc, gexc.GoogleAPIError):
        # Auth, permission, not-found or invalid-config errors: retrying will not help.
        return 502, "upstream_unavailable", None
    return 500, "internal_error", None


def _chat_error_response(operation: str, exc: Exception, language: str | None) -> JSONResponse:
    status, code, retry_after_ms = _classify_chat_error(exc)
    trace_id = get_request_id() or "-"
    # Full technical detail stays in server logs; the client gets a safe message + trace id.
    logger.exception(
        "chat.response.failed operation=%s code=%s status=%d exc_type=%s",
        operation,
        code,
        status,
        type(exc).__name__,
    )
    lang = "en" if (language or "").lower().startswith("en") else "el"
    error: dict = {"code": code, "message": _SAFE_MESSAGES[code][lang], "traceId": trace_id}
    if retry_after_ms is not None:
        error["retryAfterMs"] = retry_after_ms
    return JSONResponse(status_code=status, content={"success": False, "error": error})


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


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, description="Offset as string for pagination"),
) -> ConversationListResponse:
    """Sidebar sync: same data as /sessions, shape expected by static/chat.html."""
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


@router.delete("/session/{session_id}/history", response_model=APIResponse)
async def clear_session_history(session_id: str) -> APIResponse:
    memory = get_memory_service()
    cleared = memory.clear_session_history(session_id)
    if not cleared:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return APIResponse(success=True)


# ── Chat endpoints ───────────────────────────────────────────────────────────


@router.post("/ask", response_model=ChatResponse)
async def chat_ask(body: ChatRequest) -> ChatResponse:
    svc = ChatService()
    try:
        return await svc.ask(body)
    except Exception as exc:
        return _chat_error_response("ask", exc, body.language)


@router.post("/search", response_model=RAGSearchResponse)
async def chat_search(body: ChatRequest) -> RAGSearchResponse:
    svc = ChatService()
    try:
        results = await svc.search(body)
        return RAGSearchResponse(query=body.query, results=results)
    except Exception as exc:
        return _chat_error_response("search", exc, body.language)


@router.post("/recommend", response_model=ChatRecommendResponse)
async def chat_recommend(body: ChatRecommendRequest) -> ChatRecommendResponse:
    svc = ChatService()
    try:
        return await svc.recommend(body)
    except Exception as exc:
        return _chat_error_response("recommend", exc, body.language)


@router.post("/compare", response_model=ChatCompareResponse)
async def chat_compare(body: ChatCompareRequest) -> ChatCompareResponse:
    svc = ChatService()
    try:
        return await svc.compare(body)
    except Exception as exc:
        return _chat_error_response("compare", exc, body.language)
