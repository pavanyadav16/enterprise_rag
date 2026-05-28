"""
app/api/routes/admin.py
-----------------------
Admin dashboard REST endpoints. All routes require the admin role.

GET /api/v1/admin/health         Full system health with sub-checks
GET /api/v1/admin/vector-stats   Chunk counts per source
GET /api/v1/admin/users          All users with roles
GET /api/v1/admin/roles          All active roles
GET /api/v1/admin/roles/diagnose Diagnostic queries for role table issues
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import require_admin, UserContext

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health", summary="Full system health (admin)")
async def full_health(user: UserContext = Depends(require_admin)):
    """
    Returns detailed health information for every sub-system.
    Includes SQL Server, PGVector, embedding model, and indexing stats.
    """
    from app.db.db_manager import test_connection
    from app.db.vector_store import get_chunk_count_by_source
    from app.core.embedding_engine import status as emb_status
    from app.db.source_repository import get_all_sources

    checks: dict = {}

    # SQL Server
    try:
        ok, msg = test_connection()
        checks["sqlserver"] = {"status": "ok" if ok else "error", "detail": msg or None}
    except Exception as exc:
        checks["sqlserver"] = {"status": "error", "detail": str(exc)}

    # PGVector
    try:
        get_chunk_count_by_source()
        checks["pgvector"] = {"status": "ok"}
    except Exception as exc:
        checks["pgvector"] = {"status": "error", "detail": str(exc)}

    # Embedding model
    try:
        snap = emb_status()
        checks["embedding_model"] = {
            "status":    "ok" if snap.get("ready") else "error",
            "dimension": snap.get("dimension"),
            "detail":    snap.get("error") if not snap.get("ready") else None,
        }
    except Exception as exc:
        checks["embedding_model"] = {"status": "error", "detail": str(exc)}

    # Indexing summary
    try:
        sources = get_all_sources()
        checks["indexing"] = {
            "status":   "ok",
            "total":    len(sources),
            "indexed":  sum(1 for s in sources if s.get("index_status") == "indexed"),
            "pending":  sum(1 for s in sources if s.get("index_status") == "pending"),
            "indexing": sum(1 for s in sources if s.get("index_status") == "indexing"),
            "failed":   sum(1 for s in sources if s.get("index_status") == "failed"),
        }
    except Exception as exc:
        checks["indexing"] = {"status": "error", "detail": str(exc)}

    overall = "ok" if all(
        v.get("status") == "ok" for v in checks.values()
    ) else "degraded"

    return {"overall": overall, "checks": checks}


@router.get("/vector-stats", summary="Vector store chunk statistics")
async def vector_stats(user: UserContext = Depends(require_admin)):
    """Returns active/inactive chunk counts and last indexed timestamp per source."""
    try:
        from app.db.vector_store import get_chunk_count_by_source
        stats = get_chunk_count_by_source()
        return {"stats": stats}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not fetch vector stats: {exc}")


@router.get("/users", summary="List all users")
async def list_users(user: UserContext = Depends(require_admin)):
    """Returns all active users with their assigned role names."""
    from app.db.user_repository import get_all_users
    users, err = get_all_users()
    if err:
        raise HTTPException(status_code=500, detail=f"Could not load users: {err}")
    return {"users": users}


@router.get("/roles", summary="List all active roles")
async def list_roles(user: UserContext = Depends(require_admin)):
    """Returns all active roles of type 1 from CIS_MAST_ROLE."""
    from app.db.user_repository import get_all_roles
    roles, err = get_all_roles()
    if err:
        raise HTTPException(status_code=500, detail=f"Could not load roles: {err}")
    return {"roles": roles}


@router.get("/roles/diagnose", summary="Diagnose role table issues")
async def diagnose_roles(user: UserContext = Depends(require_admin)):
    """
    Runs step-by-step diagnostic queries against CIS_MAST_ROLE.
    Useful when the roles list appears empty in the UI.
    """
    from app.db.user_repository import diagnose_roles_table
    result = diagnose_roles_table()
    return result
