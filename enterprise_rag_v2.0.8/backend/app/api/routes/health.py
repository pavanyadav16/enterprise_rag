"""
app/api/routes/health.py
------------------------
Health check endpoints — no authentication required.
Used by Docker HEALTHCHECK, Nginx upstream checks, and monitoring tools.
"""

import logging
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.utils.properties_loader import props

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health", summary="Overall system health")
async def health():
    """
    Returns HTTP 200 when the API process is running.
    Performs non-blocking sub-checks on SQL Server, PGVector, and the
    embedding model server.
    """
    checks: dict[str, dict] = {}

    # ── SQL Server ──────────────────────────────────────────────────────────
    try:
        from app.db.db_manager import test_connection
        ok, msg = test_connection()
        checks["sqlserver"] = {"status": "ok" if ok else "error", "detail": msg or None}
    except Exception as exc:
        checks["sqlserver"] = {"status": "error", "detail": str(exc)}

    # ── PGVector ────────────────────────────────────────────────────────────
    try:
        from app.db.vector_store import get_chunk_count_by_source
        get_chunk_count_by_source()
        checks["pgvector"] = {"status": "ok"}
    except Exception as exc:
        checks["pgvector"] = {"status": "error", "detail": str(exc)}

    # ── Embedding model server ───────────────────────────────────────────────
    try:
        from app.core.embedding_engine import status as emb_status
        snap = emb_status()
        if snap.get("ready"):
            checks["embedding_model"] = {
                "status": "ok",
                "dimension": snap.get("dimension"),
            }
        else:
            checks["embedding_model"] = {
                "status": "error",
                "detail": snap.get("error", "Model server not ready"),
            }
    except Exception as exc:
        checks["embedding_model"] = {"status": "error", "detail": str(exc)}

    overall = "ok" if all(c["status"] == "ok" for c in checks.values()) else "degraded"
    status_code = 200 if overall == "ok" else 207

    return JSONResponse(
        status_code=status_code,
        content={
            "status": overall,
            "version": props.get("app.version"),
            "environment": props.get("app.environment"),
            "checks": checks,
        },
    )


@router.get("/health/live", summary="Liveness probe")
async def liveness():
    """Kubernetes/Docker liveness probe — returns 200 if the process is alive."""
    return {"status": "alive"}


@router.get("/health/ready", summary="Readiness probe")
async def readiness():
    """
    Readiness probe — returns 200 only when all critical dependencies are up.
    Nginx will stop routing to this container if this returns non-200.
    """
    try:
        from app.db.db_manager import test_connection
        ok, msg = test_connection()
        if not ok:
            return JSONResponse(status_code=503, content={"status": "not_ready", "reason": msg})
    except Exception as exc:
        return JSONResponse(status_code=503, content={"status": "not_ready", "reason": str(exc)})

    return {"status": "ready"}
