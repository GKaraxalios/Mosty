from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

from app.core.error_handlers import (
    generic_exception_handler,
    http_exception_handler,
    rate_limit_exception_handler,
    validation_exception_handler,
)

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.core.rate_limit import limiter
from app.routers import admin, chat, chat_shared, health, jobs, outfit_builder, products, rag, scheduler, commerce_features

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info(
        "%s v%s starting — GraphQL=%s",
        settings.APP_NAME,
        settings.APP_VERSION,
        settings.MAGENTO_GRAPHQL_URL,
    )

    # Start scheduler if enabled
    if settings.ENABLE_SCHEDULER:
        from app.services.scheduler_service import start_scheduler
        await start_scheduler()

    yield

    # Stop scheduler on shutdown
    from app.services.scheduler_service import stop_scheduler
    await stop_scheduler()
    logger.info("%s shutting down", settings.APP_NAME)


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=(
            "MostyAPI — Advanced RAG commerce platform.\n\n"
            "REST proxy + hybrid RAG backend for e-shop products "
            "(Magento GraphQL → Vertex AI).\n\n"
            "Features: hybrid search, reranking, conversational memory, "
            "background jobs, scheduled ingestion, caching, "
            "multilingual Greek/English shopping assistant."
        ),
        lifespan=lifespan,
    )

    # ── Rate Limiting ────────────────────────────────────────────────────
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exception_handler)

    @app.middleware("http")
    async def trace_id_middleware(request: Request, call_next):
        if not getattr(request.state, "trace_id", None):
            request.state.trace_id = uuid.uuid4().hex[:12]
        response = await call_next(request)
        response.headers["X-Trace-Id"] = request.state.trace_id
        return response

    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/chat" or path.startswith("/shared/chat") or path == "/outfit-builder":
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "base-uri 'self'; "
                "frame-ancestors 'none'; "
                "script-src 'self' https://cdn.jsdelivr.net https://fonts.googleapis.com 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com data:; "
                "img-src 'self' data: https: blob:; "
                "connect-src 'self' https://cdn.jsdelivr.net; "
                "frame-src 'self' https://www.google.com https://maps.google.com https://www.openstreetmap.org; "
                "upgrade-insecure-requests"
            )
        return response

    # ── CORS ─────────────────────────────────────────────────────────────
    origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
        allow_methods=[m.strip() for m in settings.CORS_ALLOW_METHODS.split(",") if m.strip()],
        allow_headers=[h.strip() for h in settings.CORS_ALLOW_HEADERS.split(",") if h.strip()],
    )

    # ── Routers ──────────────────────────────────────────────────────────
    prefix = settings.API_V1_PREFIX
    app.include_router(health.router, prefix=prefix)
    app.include_router(commerce_features.router, prefix=prefix)
    app.include_router(products.router, prefix=prefix)
    app.include_router(rag.router, prefix=prefix)
    app.include_router(jobs.router, prefix=prefix)
    app.include_router(scheduler.router, prefix=prefix)
    app.include_router(chat.router, prefix=prefix)
    app.include_router(chat_shared.router, prefix=prefix)
    app.include_router(outfit_builder.router, prefix=prefix)
    app.include_router(admin.router, prefix=prefix)

    # ── Static files (favicon, etc.) ─────────────────────────────────────
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    favicon_path = STATIC_DIR / "favicon.png"
    if favicon_path.exists():

        @app.get("/favicon.ico", include_in_schema=False)
        async def favicon():
            return FileResponse(favicon_path, media_type="image/png")

    # ── Chat UI ──────────────────────────────────────────────────────────
    @app.get("/chat", response_class=HTMLResponse, tags=["chat-ui"])
    async def chat_ui():
        html = (STATIC_DIR / "chat.html").read_text(encoding="utf-8")
        html = html.replace("__MOSTY_API_KEY__", settings.MOSTY_RAG_API_KEY)
        html = html.replace("__CHAT_MODE__", "interactive")
        html = html.replace("__SHARED_TOKEN__", "")
        html = html.replace(
            "__CHAT_HIDE_API_KEY_UI__",
            "true" if settings.CHAT_HIDE_API_KEY_UI else "false",
        )
        return HTMLResponse(html)

    @app.get("/shared/chat/{token}", response_class=HTMLResponse, tags=["chat-ui"])
    async def shared_chat_ui(token: str):
        html = (STATIC_DIR / "chat.html").read_text(encoding="utf-8")
        html = html.replace("__MOSTY_API_KEY__", "")
        html = html.replace("__CHAT_MODE__", "shared_interactive")
        html = html.replace("__SHARED_TOKEN__", token)
        html = html.replace("__CHAT_HIDE_API_KEY_UI__", "true")
        response = HTMLResponse(html)
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response

    @app.get(f"{prefix}/chat/bootstrap", tags=["chat-ui"])
    async def chat_bootstrap(request: Request):
        """Public env for the /chat client (no secrets). Same-origin paths resolve correctly behind proxies."""
        base = str(request.base_url).rstrip("/")
        max_chars = settings.CHAT_MAX_INPUT_CHARS
        return JSONResponse(
            {
                "success": True,
                "env": {
                    "origin": base,
                    "apiBaseUrl": f"{base}{prefix}/",
                    "staticBaseUrl": f"{base}/static/",
                },
                "features": {
                    "streaming": False,
                    "attachments": True,
                    "mapPreview": settings.CHAT_ENABLE_MAP_PREVIEW,
                    "mapsOpenInNewTab": settings.CHAT_MAPS_OPEN_IN_NEW_TAB,
                },
                "limits": {"maxInputChars": max_chars},
            }
        )

    @app.get("/outfit-builder", response_class=HTMLResponse, tags=["outfit-builder-ui"])
    async def outfit_builder_ui():
        html = (STATIC_DIR / "outfit-builder.html").read_text(encoding="utf-8")
        html = html.replace("__MOSTY_API_KEY__", settings.MOSTY_RAG_API_KEY)
        html = html.replace("__API_V1_PREFIX__", json.dumps(settings.API_V1_PREFIX))
        return HTMLResponse(html)

    @app.get(f"{prefix}/chat/ui-config", tags=["chat-ui"])
    async def chat_ui_config(variant: str = Query("production", description="Schema file: chat-page.{variant}.json")):
        """JSON page schema for the /chat UI (same contract as static/schemas/chat-page.{variant}.json)."""
        safe = variant.replace("/", "").replace("..", "") or "production"
        path = STATIC_DIR / "schemas" / f"chat-page.{safe}.json"
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"Unknown chat UI variant: {safe}")
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))

    # Commerce dashboard (keys injected server-side; opens directly)
    commerce_dash_path = STATIC_DIR / "commerce_dashboard.html"
    if commerce_dash_path.exists():
        @app.get("/commerce-dashboard", response_class=HTMLResponse, tags=["commerce-ui"])
        async def commerce_dashboard():
            html = commerce_dash_path.read_text(encoding="utf-8")
            html = html.replace("__API_PREFIX__", json.dumps(prefix))
            html = html.replace("__MOSTY_API_KEY__", json.dumps(settings.MOSTY_RAG_API_KEY or ""))
            html = html.replace("__ADMIN_API_KEY__", json.dumps(settings.ADMIN_API_KEY or ""))
            return HTMLResponse(html)

    # Admin AI evals dashboard (key injected server-side; opens directly)
    dashboard_path = STATIC_DIR / "admin_ai_evals_dashboard.html"
    if dashboard_path.exists():
        @app.get("/admin/ai-evals-dashboard", response_class=HTMLResponse, tags=["admin-ui"])
        async def admin_ai_evals_dashboard():
            html = dashboard_path.read_text(encoding="utf-8")
            html = html.replace("__API_PREFIX__", json.dumps(prefix))
            html = html.replace("__ADMIN_API_KEY__", json.dumps(settings.ADMIN_API_KEY or ""))
            return HTMLResponse(html)

    # ── Root ─────────────────────────────────────────────────────────────
    @app.get("/", tags=["root"])
    async def root():
        return {
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "docs": "/docs",
            "chat": "/chat",
            "outfit_builder": "/outfit-builder",
            "commerce_dashboard": "/commerce-dashboard",
            "health": f"{prefix}/health",
            "endpoints": {
                "products": f"{prefix}/products",
                "rag_sync": f"{prefix}/rag/sync",
                "rag_search": f"{prefix}/rag/search",
                "rag_ask": f"{prefix}/rag/ask",
                "hybrid_search": f"{prefix}/rag/hybrid-search",
                "chat_ask": f"{prefix}/chat/ask",
                "chat_bootstrap": f"{prefix}/chat/bootstrap",
                "outfit_builder_bootstrap": f"{prefix}/outfit-builder/bootstrap",
                "outfit_builder_search": f"{prefix}/outfit-builder/search-slot",
                "chat_ui_config": f"{prefix}/chat/ui-config",
                "chat_shared": f"{prefix}/chat/shared/{{token}}",
                "chat_recommend": f"{prefix}/chat/recommend",
                "chat_compare": f"{prefix}/chat/compare",
                "jobs": f"{prefix}/rag/jobs",
                "scheduler": f"{prefix}/rag/scheduler/status",
                "commerce_profile": f"{prefix}/commerce/profile/{{session_id}}",
                "commerce_wishlist": f"{prefix}/commerce/wishlist/{{session_id}}",
                "commerce_looks": f"{prefix}/commerce/looks/{{session_id}}",
                "commerce_feedback": f"{prefix}/commerce/feedback",
                "commerce_analytics": f"{prefix}/commerce/analytics/summary",
                "commerce_contradictions": f"{prefix}/commerce/filters/check-contradictions",
                "commerce_complete_look": f"{prefix}/commerce/complete-the-look",
                "commerce_compare": f"{prefix}/commerce/compare/structured",
                "commerce_alternatives": f"{prefix}/commerce/alternatives",
                "commerce_alerts": f"{prefix}/commerce/alerts/{{session_id}}",
                "admin_ai_evals": f"{prefix}/admin/ai-evals",
                "admin_ai_evals_summary": f"{prefix}/admin/ai-evals/summary",
                "admin_merchandising": f"{prefix}/admin/merchandising/rules",
            },
        }

    # ── Exception handlers ───────────────────────────────────────────────
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    return app


app = create_app()
