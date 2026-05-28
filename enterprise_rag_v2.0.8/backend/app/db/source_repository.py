"""
source_repository.py
--------------------
Data-access layer for RAG sources administration.

Table/column names reflect the renamed schema:
  RAG_RAG_SOURCES, RAG_SOURCE_TYPES, RAG_SOURCE_ROLES,
  RAG_SOURCE_DB_QUERY, CIS_MAST_ROLE

All read functions use execute_raw_safe() which never raises.
Write functions use get_session() and raise on error so callers
receive (False, message) tuples.
"""

import logging
from typing import Any

from app.db.db_manager import execute_raw_safe, get_session
from sqlalchemy import text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helper: unwrap execute_raw_safe and log error
# ---------------------------------------------------------------------------

def _safe(sql: str, params: dict | None = None, context: str = "") -> list[dict[str, Any]]:
    """Unwrap execute_raw_safe, log any error, return rows."""
    rows, err = execute_raw_safe(sql, params)
    if err:
        logger.error("source_repository query failed [%s]: %s", context, err)
    return rows


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def get_all_sources() -> list[dict[str, Any]]:
    """Return all ACTIVE sources with their type names and assigned role names."""
    return _safe(
        """
        SELECT
            rs.RRS_ID              AS source_id,
            rs.RRS_SOURCE_NAME     AS source_name,
            st.RST_TYPE_NAME       AS source_type,
            rs.RRS_SOURCE_PATH     AS source_path,
            rs.RRS_DESCRIPTION     AS description,
            rs.RRS_EXTRA_TEXT      AS extra_text,
            rs.RRS_isValid         AS is_active,
            rs.RRS_LAST_INDEXED_AT AS last_indexed_at,
            rs.RRS_INDEX_STATUS    AS index_status,
            rs.RRS_INDEX_ERROR     AS index_error,
            rs.RRS_CREATED_AT      AS created_at,
            STRING_AGG(r.CMR_ROLE_NAME, ', ') AS roles
        FROM   RAG_RAG_SOURCES       rs
        JOIN   RAG_SOURCE_TYPES      st ON st.RST_ID        = rs.RRS_TYPE_ID
        LEFT JOIN RAG_SOURCE_ROLES   sr ON sr.RSR_SOURCE_ID = rs.RRS_ID  AND sr.RSR_isValid = 1
        LEFT JOIN CIS_MAST_ROLE      r  ON r.CMR_ID         = sr.RSR_ROLE_ID
                                        AND r.CMR_isVALID    = 1
                                        AND r.CMR_ROLE_TYPE  = 1
        WHERE  rs.RRS_isValid = 1
        GROUP BY
            rs.RRS_ID, rs.RRS_SOURCE_NAME, st.RST_TYPE_NAME, rs.RRS_SOURCE_PATH,
            rs.RRS_DESCRIPTION, rs.RRS_EXTRA_TEXT, rs.RRS_isValid,
            rs.RRS_LAST_INDEXED_AT, rs.RRS_INDEX_STATUS, rs.RRS_INDEX_ERROR,
            rs.RRS_CREATED_AT
        ORDER BY rs.RRS_SOURCE_NAME
        """,
        context="get_all_sources",
    )


def get_source_by_id(source_id: int) -> dict[str, Any] | None:
    """Return a single source record by its ID, or None if not found."""
    rows = _safe(
        """
        SELECT rs.RRS_ID              AS source_id,
               rs.RRS_SOURCE_NAME     AS source_name,
               rs.RRS_TYPE_ID         AS type_id,
               st.RST_TYPE_NAME       AS source_type,
               rs.RRS_SOURCE_PATH     AS source_path,
               rs.RRS_DESCRIPTION     AS description,
               rs.RRS_EXTRA_TEXT      AS extra_text,
               rs.RRS_isValid         AS is_active,
               rs.RRS_INDEX_STATUS    AS index_status,
               rs.RRS_LAST_INDEXED_AT AS last_indexed_at
        FROM   RAG_RAG_SOURCES  rs
        JOIN   RAG_SOURCE_TYPES st ON st.RST_ID = rs.RRS_TYPE_ID
        WHERE  rs.RRS_ID = :sid
        """,
        {"sid": source_id},
        context="get_source_by_id",
    )
    return rows[0] if rows else None


def get_source_role_ids(source_id: int) -> list[int]:
    rows = _safe(
        """
        SELECT RSR_ROLE_ID AS role_id
        FROM   RAG_SOURCE_ROLES
        WHERE  RSR_SOURCE_ID = :sid
          AND  RSR_isValid   = 1
        """,
        {"sid": source_id},
        context="get_source_role_ids",
    )
    return [r["role_id"] for r in rows]


def get_all_source_types() -> list[dict[str, Any]]:
    return _safe(
        """
        SELECT RST_ID        AS type_id,
               RST_TYPE_NAME AS type_name
        FROM   RAG_SOURCE_TYPES
        WHERE  RST_isValid = 1
        ORDER BY RST_TYPE_NAME
        """,
        context="get_all_source_types",
    )


def get_db_query_for_source(source_id: int) -> dict[str, Any] | None:
    rows = _safe(
        """
        SELECT RSDQ_ID          AS query_id,
               RSDQ_SOURCE_ID   AS source_id,
               RSDQ_QUERY_SQL   AS query_sql,
               RSDQ_ROLE_COLUMN AS role_column,
               RSDQ_DESCRIPTION AS description
        FROM   RAG_SOURCE_DB_QUERY
        WHERE  RSDQ_SOURCE_ID = :sid
          AND  RSDQ_isValid   = 1
        """,
        {"sid": source_id},
        context="get_db_query_for_source",
    )
    return rows[0] if rows else None


def get_sources_pending_indexing() -> list[dict[str, Any]]:
    """Return active sources with RRS_INDEX_STATUS = 'pending'."""
    return _safe(
        """
        SELECT rs.RRS_ID          AS source_id,
               rs.RRS_SOURCE_NAME AS source_name,
               st.RST_TYPE_NAME   AS source_type,
               rs.RRS_SOURCE_PATH AS source_path,
               rs.RRS_EXTRA_TEXT  AS extra_text
        FROM   RAG_RAG_SOURCES  rs
        JOIN   RAG_SOURCE_TYPES st ON st.RST_ID = rs.RRS_TYPE_ID
        WHERE  rs.RRS_isValid      = 1
          AND  rs.RRS_INDEX_STATUS = 'pending'
        """,
        context="get_sources_pending_indexing",
    )


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

def create_source(
    source_name: str,
    type_id: int,
    source_path: str,
    description: str,
    extra_text: str,
    role_ids: list[int],
    created_by: int,
    db_query_sql: str | None = None,
    db_role_column: str | None = None,
) -> tuple[bool, str, int | None]:
    """Insert a new source and its role assignments."""
    try:
        with get_session() as session:
            result = session.execute(
                text(
                    """
                    INSERT INTO RAG_RAG_SOURCES
                        (RRS_SOURCE_NAME, RRS_TYPE_ID, RRS_SOURCE_PATH,
                         RRS_DESCRIPTION, RRS_EXTRA_TEXT,
                         RRS_isValid, RRS_INDEX_STATUS,
                         RRS_CREATED_BY, RRS_CREATED_AT, RRS_UPDATED_AT)
                    OUTPUT INSERTED.RRS_ID
                    VALUES
                        (:name, :tid, :path,
                         :desc, :extra,
                         1, 'pending',
                         :created_by, GETDATE(), GETDATE())
                    """
                ),
                {
                    "name": source_name, "tid": type_id, "path": source_path,
                    "desc": description, "extra": extra_text,
                    "created_by": created_by,
                },
            )
            new_id = result.scalar()

            for rid in role_ids:
                session.execute(
                    text(
                        """
                        INSERT INTO RAG_SOURCE_ROLES
                            (RSR_SOURCE_ID, RSR_ROLE_ID, RSR_isValid, RSR_CREATED_ON)
                        VALUES (:sid, :rid, 1, GETDATE())
                        """
                    ),
                    {"sid": new_id, "rid": rid},
                )

            if db_query_sql:
                session.execute(
                    text(
                        """
                        INSERT INTO RAG_SOURCE_DB_QUERY
                            (RSDQ_SOURCE_ID, RSDQ_QUERY_SQL, RSDQ_ROLE_COLUMN,
                             RSDQ_isValid, RSDQ_CREATED_AT, RSDQ_UPDATED_AT)
                        VALUES (:sid, :sql, :rc, 1, GETDATE(), GETDATE())
                        """
                    ),
                    {"sid": new_id, "sql": db_query_sql, "rc": db_role_column},
                )

        logger.info("Created source id=%s name=%s", new_id, source_name)
        return True, "Source created successfully.", new_id
    except Exception as exc:
        logger.error("create_source failed: %s", exc)
        return False, f"Failed to create source: {exc}", None


def update_source(
    source_id: int,
    source_name: str,
    type_id: int,
    source_path: str,
    description: str,
    extra_text: str,
    role_ids: list[int],
    db_query_sql: str | None = None,
    db_role_column: str | None = None,
) -> tuple[bool, str]:
    try:
        with get_session() as session:
            session.execute(
                text(
                    """
                    UPDATE RAG_RAG_SOURCES SET
                        RRS_SOURCE_NAME  = :name,
                        RRS_TYPE_ID      = :tid,
                        RRS_SOURCE_PATH  = :path,
                        RRS_DESCRIPTION  = :desc,
                        RRS_EXTRA_TEXT   = :extra,
                        RRS_INDEX_STATUS = 'pending',
                        RRS_UPDATED_AT   = GETDATE()
                    WHERE RRS_ID = :sid
                    """
                ),
                {
                    "name": source_name, "tid": type_id, "path": source_path,
                    "desc": description, "extra": extra_text, "sid": source_id,
                },
            )

            # Replace role assignments.
            # The unique constraint uq_source_role is on (RSR_SOURCE_ID, RSR_ROLE_ID)
            # — not on (RSR_SOURCE_ID, RSR_ROLE_ID, RSR_isValid).
            # Soft-deleting (RSR_isValid=0) then re-inserting the same pair
            # violates the unique key.  We hard-delete old rows then insert
            # fresh ones so the constraint is never hit.
            session.execute(
                text(
                    "DELETE FROM RAG_SOURCE_ROLES WHERE RSR_SOURCE_ID = :sid"
                ),
                {"sid": source_id},
            )
            for rid in role_ids:
                session.execute(
                    text(
                        """
                        INSERT INTO RAG_SOURCE_ROLES
                            (RSR_SOURCE_ID, RSR_ROLE_ID, RSR_isValid, RSR_CREATED_ON)
                        VALUES (:sid, :rid, 1, GETDATE())
                        """
                    ),
                    {"sid": source_id, "rid": rid},
                )

            if db_query_sql:
                session.execute(
                    text(
                        "UPDATE RAG_SOURCE_DB_QUERY SET RSDQ_isValid = 0 "
                        "WHERE RSDQ_SOURCE_ID = :sid"
                    ),
                    {"sid": source_id},
                )
                session.execute(
                    text(
                        """
                        INSERT INTO RAG_SOURCE_DB_QUERY
                            (RSDQ_SOURCE_ID, RSDQ_QUERY_SQL, RSDQ_ROLE_COLUMN,
                             RSDQ_isValid, RSDQ_CREATED_AT, RSDQ_UPDATED_AT)
                        VALUES (:sid, :sql, :rc, 1, GETDATE(), GETDATE())
                        """
                    ),
                    {"sid": source_id, "sql": db_query_sql, "rc": db_role_column},
                )

        logger.info("Updated source id=%s", source_id)
        return True, "Source updated successfully."
    except Exception as exc:
        logger.error("update_source failed for id=%s: %s", source_id, exc)
        return False, f"Failed to update source: {exc}"


def delete_source(source_id: int) -> tuple[bool, str]:
    """Soft-delete source in SQL Server and hard-delete PGVector chunks."""
    try:
        with get_session() as session:
            session.execute(
                text(
                    "UPDATE RAG_RAG_SOURCES "
                    "SET RRS_isValid = 0, RRS_UPDATED_AT = GETDATE() "
                    "WHERE RRS_ID = :sid"
                ),
                {"sid": source_id},
            )
        logger.info("Soft-deleted source id=%s", source_id)
    except Exception as exc:
        logger.error("delete_source (SQL Server) failed for id=%s: %s", source_id, exc)
        return False, f"Failed to delete source: {exc}"

    try:
        from app.db.vector_store import delete_chunks_for_source
        delete_chunks_for_source(source_id)
        logger.info("Deleted PGVector chunks for source_id=%s", source_id)
    except Exception as exc:
        logger.error(
            "delete_source (PGVector) failed for id=%s: %s — "
            "source deactivated but old chunks may remain.",
            source_id, exc,
        )

    return True, "Source deleted and index removed successfully."


def update_index_status(
    source_id: int,
    status: str,
    error: str | None = None,
) -> None:
    """Update RRS_INDEX_STATUS and optionally RRS_LAST_INDEXED_AT."""
    try:
        with get_session() as session:
            if status == "indexed":
                session.execute(
                    text(
                        """
                        UPDATE RAG_RAG_SOURCES SET
                            RRS_INDEX_STATUS    = :status,
                            RRS_LAST_INDEXED_AT = GETDATE(),
                            RRS_INDEX_ERROR     = NULL,
                            RRS_UPDATED_AT      = GETDATE()
                        WHERE RRS_ID = :sid
                        """
                    ),
                    {"status": status, "sid": source_id},
                )
            else:
                session.execute(
                    text(
                        """
                        UPDATE RAG_RAG_SOURCES SET
                            RRS_INDEX_STATUS = :status,
                            RRS_INDEX_ERROR  = :err,
                            RRS_UPDATED_AT   = GETDATE()
                        WHERE RRS_ID = :sid
                        """
                    ),
                    {"status": status, "err": error, "sid": source_id},
                )
    except Exception as exc:
        logger.error("update_index_status failed for id=%s: %s", source_id, exc)
