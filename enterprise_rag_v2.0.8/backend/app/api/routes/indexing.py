"""
app/api/routes/indexing.py
--------------------------
REST endpoints for indexing control. Admin-only.

POST /api/v1/indexing/refresh-all        Re-index every active source
POST /api/v1/indexing/refresh/{id}       Re-index a single source
GET  /api/v1/indexing/status             Current indexing status report
"""

import logging
import threading

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.core.dependencies import require_admin, UserContext
from app.db.source_repository import get_all_sources, get_source_by_id

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/status", summary="Indexing status for all sources")
async def indexing_status(user: UserContext = Depends(require_admin)):
    """Returns current index_status and last_indexed_at for every active source."""
    sources = get_all_sources()
    report = [
        {
            "source_id":      s["source_id"],
            "source_name":    s["source_name"],
            "source_type":    s["source_type"],
            "index_status":   s.get("index_status"),
            "last_indexed_at": str(s.get("last_indexed_at") or ""),
            "index_error":    s.get("index_error"),
        }
        for s in sources
    ]
    totals = {
        "total":    len(sources),
        "indexed":  sum(1 for s in sources if s.get("index_status") == "indexed"),
        "pending":  sum(1 for s in sources if s.get("index_status") == "pending"),
        "indexing": sum(1 for s in sources if s.get("index_status") == "indexing"),
        "failed":   sum(1 for s in sources if s.get("index_status") == "failed"),
    }
    return {"totals": totals, "sources": report}


@router.post("/refresh-all", status_code=202, summary="Re-index all active sources")
async def refresh_all(user: UserContext = Depends(require_admin)):
    """
    Marks every active source as 'pending' and starts a background indexing run.
    Returns 202 Accepted immediately.
    """
    def _bg():
        try:
            from app.indexing.indexing_engine import index_all_sources
            result = index_all_sources()
            logger.info("refresh-all complete: %s", result)
        except Exception as exc:
            logger.error("refresh-all thread error: %s", exc)

    threading.Thread(target=_bg, daemon=True, name="RefreshAll").start()
    return JSONResponse(
        status_code=202,
        content={"message": "Full re-indexing started.", "status": "accepted"},
    )


@router.post("/refresh/{source_id}", status_code=202, summary="Re-index a single source")
async def refresh_single(
    source_id: int,
    user: UserContext = Depends(require_admin),
):
    """Re-indexes one source by ID. Returns 202 Accepted immediately."""
    src = get_source_by_id(source_id)
    if not src:
        raise HTTPException(status_code=404, detail=f"Source {source_id} not found.")

    def _bg():
        try:
            from app.indexing.indexing_engine import index_source
            success, msg = index_source(source_id)
            if not success:
                logger.error("refresh source %d failed: %s", source_id, msg)
        except Exception as exc:
            logger.error("refresh source %d thread error: %s", source_id, exc)

    threading.Thread(target=_bg, daemon=True, name=f"Refresh-{source_id}").start()
    return JSONResponse(
        status_code=202,
        content={
            "message": f"Re-indexing started for source {source_id}.",
            "status": "accepted",
        },
    )
