from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
import hashlib

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.core.log_context import get_request_id
from app.core.logging import get_logger, setup_logging
from app.middleware.logging_middleware import RequestLoggingMiddleware
from app.routers import admin, chat, health, jobs, products, rag, scheduler, commerce_features
from app.utils.store_urls import get_storefront_origin

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _compute_asset_version(rel_path: str) -> str:
    """Return short content hash for static asset (used for cache-busting)."""
    try:
        path = STATIC_DIR / rel_path
        data = path.read_bytes()
        return hashlib.sha1(data).hexdigest()[:8]
    except Exception:
        return "1"


ASSET_VERSIONS = {
    "mosty-theme.css": _compute_asset_version("css/mosty-theme.css"),
    "chat-page-renderer.js": _compute_asset_version("js/chat-page-renderer.js"),
    "mosty-logo-mark.js": _compute_asset_version("js/mosty-logo-mark.js"),
}


def _apply_asset_versions(html: str) -> str:
    out = html
    for name, ver in ASSET_VERSIONS.items():
        placeholder = f"__VER_{name}__"
        out = out.replace(placeholder, ver)
    return out

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

    # Outermost: runs first on each request — sets correlation id for all log lines.
    app.add_middleware(RequestLoggingMiddleware)

    # ── Routers ──────────────────────────────────────────────────────────
    prefix = settings.API_V1_PREFIX
    app.include_router(health.router, prefix=prefix)
    app.include_router(commerce_features.router, prefix=prefix)
    app.include_router(products.router, prefix=prefix)
    app.include_router(rag.router, prefix=prefix)
    app.include_router(jobs.router, prefix=prefix)
    app.include_router(scheduler.router, prefix=prefix)
    app.include_router(chat.router, prefix=prefix)
    app.include_router(admin.router, prefix=prefix)

    # ── Static files (favicon, etc.) ─────────────────────────────────────
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    favicon_path = STATIC_DIR / "favicon.png"
    if favicon_path.exists():

        @app.get("/favicon.ico", include_in_schema=False)
        async def favicon():
            return FileResponse(favicon_path, media_type="image/png")

    # ── Chat UI ──────────────────────────────────────────────────────────
    def _render_chat_html(
        *,
        api_key: str,
        chat_mode: str,
        shared_token: str,
        hide_api_key_ui: bool,
    ) -> str:
        html = (STATIC_DIR / "chat.html").read_text(encoding="utf-8")
        html = _apply_asset_versions(html)
        html = html.replace("__MOSTY_API_KEY__", api_key)
        html = html.replace("__CHAT_MODE__", chat_mode)
        html = html.replace("__SHARED_TOKEN__", shared_token)
        html = html.replace(
            "__CHAT_HIDE_API_KEY_UI__",
            "true" if hide_api_key_ui else "false",
        )
        return html

    @app.get("/chat", response_class=HTMLResponse, tags=["chat-ui"])
    async def chat_ui():
        html = _render_chat_html(
            api_key=settings.MOSTY_RAG_API_KEY or "",
            chat_mode="interactive",
            shared_token="",
            hide_api_key_ui=settings.CHAT_HIDE_API_KEY_UI,
        )
        return HTMLResponse(html)

    @app.get("/shared/chat/{token}", response_class=HTMLResponse, tags=["chat-ui"])
    async def shared_chat_ui(token: str):
        html = _render_chat_html(
            api_key="",
            chat_mode="shared_interactive",
            shared_token=token,
            hide_api_key_ui=True,
        )
        response = HTMLResponse(html)
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response

    @app.get(f"{prefix}/chat/bootstrap", tags=["chat-ui"])
    async def chat_bootstrap(request: Request):
        """Public env for the /chat client (no secrets). Same-origin paths resolve correctly behind proxies."""
        base = str(request.base_url).rstrip("/")
        store_origin = get_storefront_origin()
        return JSONResponse(
            {
                "success": True,
                "env": {
                    "origin": base,
                    "apiBaseUrl": f"{base}{prefix}/",
                    "staticBaseUrl": f"{base}/static/",
                    "storePublicOrigin": store_origin,
                },
                "features": {
                    "streaming": False,
                    "attachments": True,
                    "mapPreview": settings.CHAT_ENABLE_MAP_PREVIEW,
                    "mapsOpenInNewTab": settings.CHAT_MAPS_OPEN_IN_NEW_TAB,
                },
                "limits": {"maxInputChars": settings.CHAT_MAX_INPUT_CHARS},
            }
        )

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
            "commerce_dashboard": "/commerce-dashboard",
            "health": f"{prefix}/health",
            "endpoints": {
                "products": f"{prefix}/products",
                "rag_sync": f"{prefix}/rag/sync",
                "rag_search": f"{prefix}/rag/search",
                "rag_ask": f"{prefix}/rag/ask",
                "hybrid_search": f"{prefix}/rag/hybrid-search",
                "chat_ask": f"{prefix}/chat/ask",
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

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        logger.warning(
            "validation_error path=%s request_id=%s errors=%d",
            request.url.path,
            get_request_id() or "-",
            len(exc.errors()),
        )
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error": "Validation error",
                "detail": exc.errors(),
            },
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        logger.exception(
            "unhandled_exception path=%s request_id=%s: %s",
            request.url.path,
            get_request_id() or "-",
            exc,
        )
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "Internal server error",
                "detail": str(exc) if settings.DEBUG else None,
            },
        )

    return app


app = create_app()
