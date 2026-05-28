"""
chat_repository.py
------------------
Data-access layer for chat sessions and messages.

Table/column names reflect the renamed schema:
  RAG_CHAT_SESSIONS  — one row per browser session
  RAG_CHAT_MESSAGES  — one row per user or assistant message
"""

import logging
import uuid
from typing import Any

from app.db.db_manager import execute_raw_safe, get_session
from sqlalchemy import text

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def create_chat_session(user_id: int, ip_address: str | None = None) -> str | None:
    """
    Insert a new chat session row and return its UUID string.
    Returns None on failure so caller continues without session tracking.
    """
    session_id = str(uuid.uuid4())
    try:
        with get_session() as db:
            db.execute(
                text(
                    """
                    INSERT INTO RAG_CHAT_SESSIONS
                        (RCS_SESSION_ID, RCS_USER_ID, RCS_STARTED_AT, RCS_IP_ADDRESS)
                    VALUES (:sid, :uid, GETDATE(), :ip)
                    """
                ),
                {"sid": session_id, "uid": user_id, "ip": ip_address},
            )
        logger.debug("Created chat session %s for user_id=%s", session_id, user_id)
        return session_id
    except Exception as exc:
        logger.error("create_chat_session failed for user_id=%s: %s", user_id, exc)
        return None


def close_chat_session(session_id: str) -> None:
    """Mark a chat session as ended."""
    if not session_id:
        return
    try:
        with get_session() as db:
            db.execute(
                text(
                    "UPDATE RAG_CHAT_SESSIONS SET RCS_ENDED_AT = GETDATE() "
                    "WHERE RCS_SESSION_ID = :sid"
                ),
                {"sid": session_id},
            )
        logger.debug("Closed chat session %s", session_id)
    except Exception as exc:
        logger.error("close_chat_session failed for %s: %s", session_id, exc)


# ---------------------------------------------------------------------------
# Message persistence
# ---------------------------------------------------------------------------

def save_message(
    session_id: str,
    role: str,
    content: str,
    source_ids: list[int] | None = None,
) -> bool:
    """
    Persist a single chat message to SQL Server.

    Args:
        session_id: UUID from create_chat_session().
        role:       'user' or 'assistant'.
        content:    Message text.
        source_ids: Source IDs used (assistant messages only).

    Returns:
        True on success, False on failure.
    """
    if not session_id:
        logger.debug("save_message skipped — no active session_id.")
        return False

    source_ids_str = (
        ",".join(str(s) for s in source_ids) if source_ids else None
    )

    try:
        with get_session() as db:
            db.execute(
                text(
                    """
                    INSERT INTO RAG_CHAT_MESSAGES
                        (RCM_SESSION_ID, RCM_ROLE, RCM_CONTENT,
                         RCM_SOURCE_IDS, RCM_CREATED_AT)
                    VALUES
                        (:sid, :role, :content, :source_ids, GETDATE())
                    """
                ),
                {
                    "sid":        session_id,
                    "role":       role,
                    "content":    content,
                    "source_ids": source_ids_str,
                },
            )
        logger.debug("Saved %s message to session %s", role, session_id)
        return True
    except Exception as exc:
        logger.error(
            "save_message failed for session=%s role=%s: %s", session_id, role, exc
        )
        return False


def save_exchange(
    session_id: str,
    user_query: str,
    assistant_response: str,
    source_ids: list[int] | None = None,
) -> None:
    """Save both sides of a Q&A exchange."""
    save_message(session_id, "user",      user_query,          source_ids=None)
    save_message(session_id, "assistant", assistant_response,   source_ids=source_ids)


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def get_session_messages(session_id: str) -> list[dict[str, Any]]:
    """Return all messages for a session ordered by creation time."""
    try:
        rows, err = execute_raw_safe(
            """
            SELECT RCM_ID         AS message_id,
                   RCM_ROLE       AS role,
                   RCM_CONTENT    AS content,
                   RCM_SOURCE_IDS AS source_ids,
                   RCM_CREATED_AT AS created_at
            FROM   RAG_CHAT_MESSAGES
            WHERE  RCM_SESSION_ID = :sid
            ORDER BY RCM_CREATED_AT ASC
            """,
            {"sid": session_id},
        )
        if err:
            logger.error("get_session_messages failed for %s: %s", session_id, err)
        return rows
    except Exception as exc:
        logger.error("get_session_messages unexpected error for %s: %s", session_id, exc)
        return []


def get_recent_sessions(user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    """Return the most recent chat sessions for a user."""
    try:
        rows, err = execute_raw_safe(
            """
            SELECT TOP (:lim)
                cs.RCS_SESSION_ID AS session_id,
                cs.RCS_STARTED_AT AS started_at,
                cs.RCS_ENDED_AT   AS ended_at,
                COUNT(cm.RCM_ID)  AS message_count
            FROM   RAG_CHAT_SESSIONS cs
            LEFT JOIN RAG_CHAT_MESSAGES cm ON cm.RCM_SESSION_ID = cs.RCS_SESSION_ID
            WHERE  cs.RCS_USER_ID = :uid
            GROUP BY cs.RCS_SESSION_ID, cs.RCS_STARTED_AT, cs.RCS_ENDED_AT
            ORDER BY cs.RCS_STARTED_AT DESC
            """,
            {"uid": user_id, "lim": limit},
        )
        if err:
            logger.error("get_recent_sessions failed for user_id=%s: %s", user_id, err)
        return rows
    except Exception as exc:
        logger.error("get_recent_sessions unexpected error for user_id=%s: %s", user_id, exc)
        return []
