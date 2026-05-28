"""
vector_store.py
---------------
Manages the PGVector (PostgreSQL) store for semantic similarity search.

Locking strategy — no idle transactions
-----------------------------------------
PostgreSQL locks rows during transactions.  The original code acquired a
connection, ran a query, then called conn.commit() in _release() — this
held an open transaction the entire time the connection was checked out,
blocking concurrent writers (indexing) even for read-only queries.

Fix applied:
  - Read-only operations (_acquire_reader) use autocommit=True so
    PostgreSQL never opens a transaction at all.  No shared locks taken,
    no blocking of writers, no rollback needed.
  - Write operations (_acquire_writer) keep autocommit=False so INSERT /
    UPDATE / DELETE are wrapped in an explicit transaction with commit()
    or rollback() on completion.
  - Two separate acquire helpers make the intent explicit in every function.

Connection pool
---------------
Uses psycopg2.ThreadedConnectionPool so Streamlit sessions and background
indexing threads each get their own connection without sharing state.

connect_timeout=10 prevents indefinite hangs if PostgreSQL is unreachable.
"""

import atexit
import logging
import json
import threading
from typing import Any

import psycopg2
import psycopg2.extras
import psycopg2.pool
from pgvector.psycopg2 import register_vector

from app.utils.properties_loader import props

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thread-safe connection pool
# ---------------------------------------------------------------------------
_pool: "psycopg2.pool.ThreadedConnectionPool | None" = None
_pool_lock = threading.Lock()


def _close_pool() -> None:
    """Close all pooled PostgreSQL connections on process exit."""
    global _pool
    if _pool is not None:
        try:
            _pool.closeall()
            logger.debug("PGVector connection pool closed.")
        except Exception:
            pass


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    """
    Lazily create and return the global threaded psycopg2 connection pool.

    Uses double-checked locking so only one thread creates the pool even
    when many threads start simultaneously at app startup.
    """
    global _pool
    if _pool is not None:
        return _pool

    with _pool_lock:
        if _pool is not None:   # another thread may have created it while we waited
            return _pool

        host     = props.get("pgvector.host")
        port     = props.get_int("pgvector.port", 5432)
        dbname   = props.get("pgvector.database")
        user     = props.get("pgvector.username")
        password = props.get("pgvector.password")
        min_conn = props.get_int("pgvector.pool_size", 2)
        max_conn = min_conn + 8   # headroom for simultaneous indexing + queries

        try:
            _pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=min_conn,
                maxconn=max_conn,
                host=host,
                port=port,
                dbname=dbname,
                user=user,
                password=password,      # kwarg — safe for any special chars
                connect_timeout=10,     # fail fast if PG is unreachable
            )
            logger.info(
                "PGVector pool created: host=%s port=%s db=%s "
                "(min=%d max=%d)",
                host, port, dbname, min_conn, max_conn,
            )
            # Register cleanup so all PG connections are closed on Ctrl+C / exit.
            # psycopg2 TCP sockets held open by the pool prevent clean shutdown
            # on Windows when KeyboardInterrupt is raised.
            atexit.register(_close_pool)
            return _pool
        except Exception as exc:
            logger.error("Failed to create PGVector pool: %s", exc)
            raise


def _register(conn) -> None:
    """Register the pgvector type adapter on a connection (idempotent)."""
    try:
        register_vector(conn)
    except Exception:
        pass    # already registered on this connection — safe to ignore


def _acquire_reader() -> psycopg2.extensions.connection:
    """
    Get a connection for READ-ONLY operations.

    Sets autocommit=True so PostgreSQL never opens a transaction.
    No shared locks are ever taken — reads never block writers.
    The caller must call _release_reader() when done.
    """
    conn = _get_pool().getconn()
    conn.autocommit = True      # no implicit transaction — no locks
    _register(conn)
    return conn


def _release_reader(conn: psycopg2.extensions.connection) -> None:
    """Return a reader connection to the pool. No commit/rollback needed."""
    try:
        _get_pool().putconn(conn)
    except Exception:
        pass


def _acquire_writer() -> psycopg2.extensions.connection:
    """
    Get a connection for WRITE operations (INSERT / UPDATE / DELETE).

    autocommit=False so the caller controls transaction boundaries
    explicitly with conn.commit() or conn.rollback().
    The caller must call _release_writer() when done.
    """
    conn = _get_pool().getconn()
    conn.autocommit = False     # explicit transaction control for writes
    _register(conn)
    return conn


def _release_writer(
    conn: psycopg2.extensions.connection,
    error: bool = False,
) -> None:
    """
    Commit or roll back a writer connection then return it to the pool.

    Args:
        conn:  The writer connection to release.
        error: True → rollback; False → commit.
    """
    try:
        if error:
            conn.rollback()
        else:
            conn.commit()
    except Exception:
        pass
    try:
        _get_pool().putconn(conn)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Schema initialisation  (write)
# ---------------------------------------------------------------------------

def init_vector_store() -> None:
    """
    Ensure the pgvector extension and document_chunks table exist.

    Safe to call multiple times — all statements use IF NOT EXISTS.
    Called once at application startup from main_app.py.
    """
    dim  = props.get_int("embedding.dimension", 384)
    conn = _acquire_writer()
    err  = False
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS document_chunks (
                    id          BIGSERIAL   PRIMARY KEY,
                    source_id   INTEGER     NOT NULL,
                    chunk_index INTEGER     NOT NULL,
                    content     TEXT        NOT NULL,
                    embedding   vector({dim}),
                    metadata    JSONB,
                    is_active   BOOLEAN     NOT NULL DEFAULT TRUE,
                    indexed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_chunks_source_id
                ON document_chunks(source_id);
            """)

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_chunks_active
                ON document_chunks(is_active)
                WHERE is_active = TRUE;
            """)

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_chunks_embedding
                ON document_chunks
                USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 100);
            """)

        logger.info("Vector store schema verified (dim=%d).", dim)

    except Exception as exc:
        err = True
        logger.error("init_vector_store failed: %s", exc)
        raise
    finally:
        _release_writer(conn, error=err)


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------

def deactivate_chunks_for_source(source_id: int) -> None:
    """
    Mark all existing chunks for a source as inactive before re-indexing.

    Old chunks remain in the table for audit purposes but are excluded from
    similarity searches (is_active = FALSE).  New chunks are inserted after
    this call, achieving zero-downtime re-indexing.
    """
    conn = _acquire_writer()
    err  = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE document_chunks SET is_active = FALSE "
                "WHERE source_id = %s",
                (source_id,),
            )
        logger.debug("Deactivated chunks for source_id=%s", source_id)
    except Exception as exc:
        err = True
        logger.error(
            "deactivate_chunks_for_source failed source_id=%s: %s",
            source_id, exc,
        )
        raise
    finally:
        _release_writer(conn, error=err)


def insert_chunks(source_id: int, chunks: list[dict[str, Any]]) -> int:
    """
    Bulk-insert document chunks with their pre-computed embedding vectors.

    Uses execute_values for efficient bulk insertion.

    Args:
        source_id: FK to SQL Server RAG_RAG_SOURCES.RRS_ID.
        chunks:    List of dicts: {chunk_index, content, embedding, metadata}.

    Returns:
        Number of rows inserted.  0 if chunks is empty.
    """
    if not chunks:
        return 0

    conn = _acquire_writer()
    err  = False
    try:
        with conn.cursor() as cur:
            records = [
                (
                    source_id,
                    c["chunk_index"],
                    c["content"],
                    c["embedding"],
                    json.dumps(c.get("metadata") or {}),
                    True,
                )
                for c in chunks
            ]
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO document_chunks
                    (source_id, chunk_index, content, embedding, metadata, is_active)
                VALUES %s
                """,
                records,
                template="(%s, %s, %s, %s::vector, %s::jsonb, %s)",
            )
        logger.info("Inserted %d chunks for source_id=%s", len(chunks), source_id)
        return len(chunks)
    except Exception as exc:
        err = True
        logger.error("insert_chunks failed source_id=%s: %s", source_id, exc)
        raise
    finally:
        _release_writer(conn, error=err)


def delete_chunks_for_source(source_id: int) -> None:
    """
    Permanently delete all chunks for a source.
    Called when a source is soft-deleted from the admin UI.
    """
    conn = _acquire_writer()
    err  = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM document_chunks WHERE source_id = %s",
                (source_id,),
            )
        logger.info("Deleted chunks for source_id=%s", source_id)
    except Exception as exc:
        err = True
        logger.error(
            "delete_chunks_for_source failed source_id=%s: %s",
            source_id, exc,
        )
        raise
    finally:
        _release_writer(conn, error=err)


# ---------------------------------------------------------------------------
# Read operations  (autocommit=True — no locks)
# ---------------------------------------------------------------------------

def similarity_search(
    query_embedding: list[float],
    source_ids: list[int],
    top_k: int = 5,
    threshold: float = 0.35,
) -> list[dict[str, Any]]:
    """
    Find the top-K active chunks most similar to the query embedding.

    Uses cosine distance (embedding <=> query).
    similarity = 1 - cosine_distance.

    Runs under autocommit=True — no transaction, no shared locks,
    does not block concurrent indexing writes at all.

    Embedding parameter appears twice in the SQL:
      pos 1      : SELECT expression  (compute distance for ORDER BY)
      pos 2…N    : IN clause          (RBAC source filter)
      pos N+1    : WHERE filter       (threshold check)
      pos N+2    : threshold value
      pos N+3    : LIMIT value

    Args:
        query_embedding: Float vector from the embedding model.
        source_ids:      RBAC-filtered list of source IDs to search within.
        top_k:           Maximum number of results to return.
        threshold:       Minimum cosine similarity (0.0–1.0) to include.

    Returns:
        List of dicts: {source_id, chunk_index, content, metadata, similarity}.
        Empty list on error or when source_ids is empty.
    """
    if not source_ids:
        return []

    conn = _acquire_reader()        # autocommit=True — zero locking
    try:
        in_ph  = ",".join(["%s"] * len(source_ids))
        sql    = f"""
            SELECT
                source_id,
                chunk_index,
                content,
                metadata,
                1 - (embedding <=> %s::vector) AS similarity
            FROM  document_chunks
            WHERE is_active = TRUE
              AND source_id IN ({in_ph})
              AND 1 - (embedding <=> %s::vector) >= %s
            ORDER BY similarity DESC
            LIMIT %s;
        """
        params = (
            [query_embedding]
            + list(source_ids)
            + [query_embedding, threshold, top_k]
        )

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        logger.debug(
            "similarity_search: %d results threshold=%.2f sources=%s",
            len(rows), threshold, source_ids,
        )
        return [dict(r) for r in rows]

    except Exception as exc:
        logger.error("similarity_search failed: %s", exc)
        return []
    finally:
        _release_reader(conn)


def get_chunk_count_by_source() -> list[dict[str, Any]]:
    """
    Return chunk counts (active + inactive) per source for admin diagnostics.

    Runs under autocommit=True — no locking.
    """
    conn = _acquire_reader()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    source_id,
                    COUNT(*) FILTER (WHERE is_active = TRUE)  AS active_chunks,
                    COUNT(*) FILTER (WHERE is_active = FALSE) AS inactive_chunks,
                    MAX(indexed_at)                           AS last_indexed_at
                FROM  document_chunks
                GROUP BY source_id
                ORDER BY source_id;
            """)
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("get_chunk_count_by_source failed: %s", exc)
        return []
    finally:
        _release_reader(conn)
