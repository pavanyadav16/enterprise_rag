"""
app/api/routes/sources.py
--------------------------
REST endpoints for RAG knowledge source management.
All write operations require the admin role.

GET    /api/v1/sources/              List all active sources
GET    /api/v1/sources/types         List available source types
GET    /api/v1/sources/{id}          Get a single source
POST   /api/v1/sources/              Create a new source
PUT    /api/v1/sources/{id}          Update an existing source
DELETE /api/v1/sources/{id}          Soft-delete a source
POST   /api/v1/sources/{id}/reindex  Trigger re-indexing for a source
POST   /api/v1/sources/upload        Upload a file and return its saved path
"""

import logging
from typing import Any

from fastapi import (
    APIRouter, Depends, HTTPException, UploadFile, File, Form, status,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.dependencies import get_current_user, require_admin, UserContext
from app.db.source_repository import (
    get_all_sources, get_source_by_id, get_source_role_ids,
    get_all_source_types, get_db_query_for_source,
    create_source, update_source, delete_source,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class SourceCreateRequest(BaseModel):
    source_name: str = Field(..., max_length=300)
    type_id: int
    source_path: str = Field("", max_length=2000)
    description: str = Field("", max_length=1000)
    extra_text: str = ""
    role_ids: list[int] = []          # empty = All Roles
    db_query_sql: str | None = None
    db_role_column: str | None = None

class SourceUpdateRequest(BaseModel):
    source_name: str = Field(..., max_length=300)
    type_id: int
    source_path: str = Field("", max_length=2000)
    description: str = Field("", max_length=1000)
    extra_text: str = ""
    role_ids: list[int] = []
    db_query_sql: str | None = None
    db_role_column: str | None = None

# ---------------------------------------------------------------------------
# Routes — read (authenticated users)
# ---------------------------------------------------------------------------

@router.get("/", summary="List all active sources")
async def list_sources(user: UserContext = Depends(get_current_user)):
    """Returns all active RAG sources with their type, roles, and index status."""
    return get_all_sources()


@router.get("/types", summary="List available source types")
async def list_source_types(user: UserContext = Depends(get_current_user)):
    """Returns lookup list of supported source categories (pdf, docx, url, …)."""
    return get_all_source_types()


@router.get("/{source_id}", summary="Get a single source")
async def get_source(source_id: int, user: UserContext = Depends(get_current_user)):
    """Returns a single source record by ID, including its assigned role IDs."""
    src = get_source_by_id(source_id)
    if not src:
        raise HTTPException(status_code=404, detail=f"Source {source_id} not found.")

    role_ids = get_source_role_ids(source_id)
    db_cfg   = get_db_query_for_source(source_id)

    return {**src, "role_ids": role_ids, "db_query": db_cfg}

# ---------------------------------------------------------------------------
# Routes — write (admin only)
# ---------------------------------------------------------------------------

@router.post("/", status_code=201, summary="Create a new source")
async def create(
    body: SourceCreateRequest,
    user: UserContext = Depends(require_admin),
):
    ok, msg, new_id = create_source(
        source_name=body.source_name,
        type_id=body.type_id,
        source_path=body.source_path,
        description=body.description,
        extra_text=body.extra_text,
        role_ids=body.role_ids,
        created_by=user["user_id"],
        db_query_sql=body.db_query_sql,
        db_role_column=body.db_role_column,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    # Kick off indexing in the background
    try:
        from app.indexing.indexing_engine import refresh_source_async
        refresh_source_async(new_id)
    except Exception as exc:
        logger.warning("Could not start background indexing for new source: %s", exc)

    return {"source_id": new_id, "message": msg}


@router.put("/{source_id}", summary="Update an existing source")
async def update(
    source_id: int,
    body: SourceUpdateRequest,
    user: UserContext = Depends(require_admin),
):
    src = get_source_by_id(source_id)
    if not src:
        raise HTTPException(status_code=404, detail=f"Source {source_id} not found.")

    ok, msg = update_source(
        source_id=source_id,
        source_name=body.source_name,
        type_id=body.type_id,
        source_path=body.source_path,
        description=body.description,
        extra_text=body.extra_text,
        role_ids=body.role_ids,
        db_query_sql=body.db_query_sql,
        db_role_column=body.db_role_column,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    try:
        from app.indexing.indexing_engine import refresh_source_async
        refresh_source_async(source_id)
    except Exception as exc:
        logger.warning("Could not start re-indexing after update: %s", exc)

    return {"source_id": source_id, "message": msg}


@router.delete("/{source_id}", summary="Delete a source")
async def remove(
    source_id: int,
    user: UserContext = Depends(require_admin),
):
    src = get_source_by_id(source_id)
    if not src:
        raise HTTPException(status_code=404, detail=f"Source {source_id} not found.")

    ok, msg = delete_source(source_id)
    if not ok:
        raise HTTPException(status_code=500, detail=msg)

    return {"source_id": source_id, "message": msg}


@router.post("/{source_id}/reindex", summary="Trigger re-indexing for a source")
async def reindex(
    source_id: int,
    user: UserContext = Depends(require_admin),
):
    src = get_source_by_id(source_id)
    if not src:
        raise HTTPException(status_code=404, detail=f"Source {source_id} not found.")

    try:
        from app.indexing.indexing_engine import refresh_source_async
        refresh_source_async(source_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not start re-indexing: {exc}")

    return {"source_id": source_id, "message": "Re-indexing started in background."}


@router.post("/upload", summary="Upload a source file")
async def upload_file(
    source_type: str = Form(...),
    file: UploadFile = File(...),
    user: UserContext = Depends(require_admin),
):
    """
    Upload a file to the source-files directory.
    Returns the saved file path to be used as source_path when creating a source.
    """
    from pathlib import Path
    from app.utils.properties_loader import props

    UPLOADABLE = {"pdf", "docx", "doc", "txt", "image", "excel"}
    if source_type not in UPLOADABLE:
        raise HTTPException(
            status_code=400,
            detail=f"Type '{source_type}' does not support file upload.",
        )

    base_raw = props.get("source.upload.directory", "/app/source-files")
    base_dir = Path(base_raw)
    type_dir = base_dir / source_type
    type_dir.mkdir(parents=True, exist_ok=True)

    original_name = Path(file.filename or "upload").name
    dest = type_dir / original_name
    stem   = dest.stem
    suffix = dest.suffix
    counter = 1
    while dest.exists():
        dest = type_dir / f"{stem}_{counter}{suffix}"
        counter += 1

    try:
        content = await file.read()
        dest.write_bytes(content)
        logger.info("Uploaded source file: %s", dest)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"File save failed: {exc}")

    return {"file_path": str(dest), "filename": dest.name}
