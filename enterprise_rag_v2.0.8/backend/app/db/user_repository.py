"""
user_repository.py
------------------
Data access layer for users, roles, and source-level RBAC.

Table/column names reflect the renamed schema:
  SPE_ADMIN_USER, CIS_MAST_ROLE, CIS_MAP_USER_ROLE,
  RAG_RAG_SOURCES, RAG_SOURCE_ROLES

All functions use execute_raw_safe() so DB errors are logged and
returned as an empty list rather than crashing the UI.
"""

import logging
from typing import Any

from app.db.db_manager import execute_raw_safe, get_session
from sqlalchemy import text

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# User look-up
# ---------------------------------------------------------------------------

def get_user_by_jwt_subject(jwt_subject: str) -> dict[str, Any] | None:
    """Return user record for the given JWT 'sub' claim, or None."""
    rows, err = execute_raw_safe(
        """
        SELECT SAU_ID        AS user_id,
               SAU_LOGIN     AS jwt_subject,
               SAU_NAME      AS display_name,
               SAU_EMAIL     AS email,
               SAU_FLAG      AS is_active
        FROM   SPE_ADMIN_USER
        WHERE  SAU_LOGIN = :sub
          AND  SAU_FLAG  = 1
        """,
        {"sub": jwt_subject},
    )
    if err:
        logger.error("get_user_by_jwt_subject failed: %s", err)
    return rows[0] if rows else None


def get_user_roles(user_id: int) -> list[str]:
    """Return list of active role name strings for the given user."""
    rows, err = execute_raw_safe(
        """
        SELECT r.CMR_ROLE_NAME AS role_name
        FROM   CIS_MAP_USER_ROLE  ur
        JOIN   CIS_MAST_ROLE      r  ON r.CMR_ID  = ur.CMUR_ROLE_ID
        WHERE  ur.CMUR_USER_ID = :uid
          AND  ur.CMUR_isValid = 1
          AND  r.CMR_isVALID   = 1
          AND  r.CMR_ROLE_TYPE = 1
        """,
        {"uid": user_id},
    )
    if err:
        logger.error("get_user_roles failed for user_id=%s: %s", user_id, err)
    return [r["role_name"] for r in rows]


def is_admin(user_id: int) -> bool:
    """Check if a user holds the 'admin' role."""
    return "admin" in get_user_roles(user_id)


# ---------------------------------------------------------------------------
# Source RBAC
# ---------------------------------------------------------------------------

def get_accessible_source_ids(user_id: int) -> list[int]:
    """
    Return IDs of all active sources the user is authorised to access.

    Rules:
      - Admins → every active source.
      - Sources with NO rows in RAG_SOURCE_ROLES (All Roles) → accessible to everyone.
      - Sources WITH role rows → user must hold at least one matching role.
    """
    if is_admin(user_id):
        rows, err = execute_raw_safe(
            "SELECT RRS_ID AS source_id FROM RAG_RAG_SOURCES WHERE RRS_isValid = 1"
        )
        if err:
            logger.error("get_accessible_source_ids (admin) failed: %s", err)
        return [r["source_id"] for r in rows]

    rows, err = execute_raw_safe(
        """
        SELECT rs.RRS_ID AS source_id
        FROM   RAG_RAG_SOURCES rs
        WHERE  rs.RRS_isValid = 1
          AND (
              -- No role rows = All Roles (open access)
              NOT EXISTS (
                  SELECT 1 FROM RAG_SOURCE_ROLES sr2
                  WHERE  sr2.RSR_SOURCE_ID = rs.RRS_ID
                    AND  sr2.RSR_isValid   = 1
              )
              OR
              -- Has role rows and user holds at least one
              EXISTS (
                  SELECT 1
                  FROM   RAG_SOURCE_ROLES  sr
                  JOIN   CIS_MAP_USER_ROLE ur ON ur.CMUR_ROLE_ID = sr.RSR_ROLE_ID
                  WHERE  sr.RSR_SOURCE_ID = rs.RRS_ID
                    AND  sr.RSR_isValid   = 1
                    AND  ur.CMUR_USER_ID  = :uid
                    AND  ur.CMUR_isValid  = 1
              )
          )
        """,
        {"uid": user_id},
    )
    if err:
        logger.error("get_accessible_source_ids failed for user_id=%s: %s", user_id, err)
    return [r["source_id"] for r in rows]


def get_accessible_sources_with_names(user_id: int) -> list[dict[str, Any]]:
    """Return source_id + source_name + description for accessible sources."""
    source_ids = get_accessible_source_ids(user_id)
    if not source_ids:
        return []

    placeholders = ",".join([f":sid{i}" for i in range(len(source_ids))])
    params = {f"sid{i}": sid for i, sid in enumerate(source_ids)}
    rows, err = execute_raw_safe(
        f"""
        SELECT RRS_ID          AS source_id,
               RRS_SOURCE_NAME AS source_name,
               RRS_DESCRIPTION AS description
        FROM   RAG_RAG_SOURCES
        WHERE  RRS_ID     IN ({placeholders})
          AND  RRS_isValid = 1
        """,
        params,
    )
    if err:
        logger.error("get_accessible_sources_with_names failed: %s", err)
    return rows


# ---------------------------------------------------------------------------
# All roles / users (admin UI)
# ---------------------------------------------------------------------------

def get_all_roles() -> tuple[list[dict[str, Any]], str | None]:
    """
    Return (rows, error) for all active roles of type 1.

    Returns a tuple so the UI can display the error message when empty.
    rows keys: role_id, role_name
    """
    rows, err = execute_raw_safe(
        """
        SELECT CMR_ID        AS role_id,
               CMR_ROLE_NAME AS role_name
        FROM   CIS_MAST_ROLE
        WHERE  CMR_isVALID   = 1
          AND  CMR_ROLE_TYPE = 1
        ORDER BY CMR_ROLE_NAME
        """
    )
    if err:
        logger.error("get_all_roles failed: %s", err)
    return rows, err


def get_all_users() -> tuple[list[dict[str, Any]], str | None]:
    """
    Return (rows, error) for all active users with their role names.

    rows keys: user_id, jwt_subject, display_name, email, roles
    """
    rows, err = execute_raw_safe(
        """
        SELECT u.SAU_ID      AS user_id,
               u.SAU_LOGIN   AS jwt_subject,
               u.SAU_NAME    AS display_name,
               u.SAU_EMAIL   AS email,
               STRING_AGG(r.CMR_ROLE_NAME, ', ') AS roles
        FROM   SPE_ADMIN_USER       u
        LEFT JOIN CIS_MAP_USER_ROLE ur ON ur.CMUR_USER_ID = u.SAU_ID
                                       AND ur.CMUR_isValid = 1
        LEFT JOIN CIS_MAST_ROLE     r  ON r.CMR_ID        = ur.CMUR_ROLE_ID
                                       AND r.CMR_isVALID   = 1
                                       AND r.CMR_ROLE_TYPE = 1
        WHERE  u.SAU_FLAG = 1
        GROUP BY u.SAU_ID, u.SAU_LOGIN, u.SAU_NAME, u.SAU_EMAIL
        ORDER BY u.SAU_NAME
        """
    )
    if err:
        logger.error("get_all_users failed: %s", err)
    return rows, err


def diagnose_roles_table() -> dict[str, Any]:
    """
    Run diagnostic queries against CIS_MAST_ROLE and return findings.
    Used by the admin dashboard to show exactly why roles may be empty.
    """
    result: dict[str, Any] = {}

    # 1. Does the table exist?
    rows, err = execute_raw_safe(
        """
        SELECT COUNT(*) AS cnt
        FROM   INFORMATION_SCHEMA.TABLES
        WHERE  TABLE_NAME = 'CIS_MAST_ROLE'
        """
    )
    result["table_exists"] = (rows[0]["cnt"] > 0) if rows else False
    result["table_check_error"] = err

    if not result["table_exists"]:
        return result

    # 2. Total row count (no filters)
    rows, err = execute_raw_safe("SELECT COUNT(*) AS cnt FROM CIS_MAST_ROLE")
    result["total_rows"] = rows[0]["cnt"] if rows else 0
    result["total_rows_error"] = err

    # 3. Rows visible with filters
    rows, err = execute_raw_safe(
        "SELECT COUNT(*) AS cnt FROM CIS_MAST_ROLE WHERE CMR_isVALID = 1 AND CMR_ROLE_TYPE = 1"
    )
    result["visible_rows"] = rows[0]["cnt"] if rows else 0
    result["visible_rows_error"] = err

    # 4. Sample of all rows (up to 10, no filters) to show actual values
    rows, err = execute_raw_safe(
        "SELECT TOP 10 CMR_ID, CMR_ROLE_NAME, CMR_ROLE_TYPE, CMR_isVALID FROM CIS_MAST_ROLE"
    )
    result["sample_rows"] = rows
    result["sample_error"] = err

    return result
