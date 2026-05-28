"""
main.py
-------
FastAPI application entry point for Enterprise RAG v2.0.8.

Architecture
------------
  Nginx (port 80/443)
    ├── /api/*          → FastAPI backend  (this process, port 8000)
    ├── /               → Open WebUI       (port 8080)
    └── /auth-proxy/*   → openwebui-auth-proxy (port 8081)

Start with:
    uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4

All admin dashboard, source management and chat operations are
exposed as REST endpoints — no Streamlit dependency.
"""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# ── Project root on sys.path ────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── Bootstrap logging FIRST ─────────────────────────────────────────────────
from app.utils.logger_config import setup_logging
setup_logging()
logger = logging.getLogger(__name__)

# ── FastAPI ──────────────────────────────────────────────────────────────────
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.utils.properties_loader import props

# ── Route modules ────────────────────────────────────────────────────────────
from app.api.routes import (
    auth,
    health,
    sources,
    indexing,
    chat,
    admin,
    openwebui_integration,
)


# ---------------------------------------------------------------------------
# Lifespan: startup / shutdown tasks
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI):
    """Run startup tasks before the first request; cleanup on shutdown."""
    logger.info("=== Enterprise RAG v%s starting ===", props.get("app.version"))

    # Ensure required directories exist
    for key in [
        "source.upload.directory",
        "upload.chat_temp_directory",
    ]:
        raw = props.get(key)
        if raw:
            Path(raw).mkdir(parents=True, exist_ok=True)
            logger.info("Directory ready: %s", raw)

    # Initialise PGVector schema
    try:
        from app.db.vector_store import init_vector_store
        init_vector_store()
        logger.info("Vector store schema verified.")
    except Exception as exc:
        logger.error("Vector store init failed: %s", exc)

    # Background startup indexing
    try:
        from app.indexing.indexing_engine import startup_indexing
        startup_indexing()
    except Exception as exc:
        logger.error("Startup indexing failed: %s", exc)

    # Register RAG pipeline model in Open WebUI
    try:
        from app.api.routes.openwebui_integration import register_rag_model
        await register_rag_model()
    except Exception as exc:
        logger.warning("Open WebUI model registration skipped: %s", exc)

    yield

    logger.info("=== Enterprise RAG shutting down ===")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Enterprise RAG API",
    description=(
        "REST backend for Enterprise RAG Chatbot v2.0.8. "
        "Provides authentication, source management, indexing control, "
        "RAG chat, and Open WebUI auto-login integration."
    ),
    version=props.get("app.version", "2.0.8"),
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
_raw_origins = props.get("cors.allowed_origins", "")
_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()] or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(health.router,                prefix="/api/v1",        tags=["Health"])
app.include_router(auth.router,                  prefix="/api/v1/auth",   tags=["Authentication"])
app.include_router(sources.router,               prefix="/api/v1/sources",tags=["Sources"])
app.include_router(indexing.router,              prefix="/api/v1/indexing",tags=["Indexing"])
app.include_router(chat.router,                  prefix="/api/v1/chat",   tags=["Chat"])
app.include_router(admin.router,                 prefix="/api/v1/admin",  tags=["Admin"])
app.include_router(openwebui_integration.router, prefix="/api/v1/owui",   tags=["OpenWebUI"])

# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. Check backend logs."},
    )


@app.get("/", include_in_schema=False)
async def root():
    return {"service": "Enterprise RAG API", "version": props.get("app.version")}
