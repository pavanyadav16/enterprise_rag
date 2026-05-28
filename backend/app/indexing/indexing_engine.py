"""
indexing_engine.py
------------------
Orchestrates the full indexing pipeline for a RAG source:

  1. Load raw text from the source (file, URL, DB query, …)
  2. Chunk the text into overlapping segments
  3. Generate embeddings via the local sentence transformer
  4. Deactivate previous chunks in the vector store
  5. Insert new chunks

Public API
----------
index_source(source_id)         — index a single source by ID
index_all_pending()             — index every source with status='pending'
refresh_source(source_id)       — force re-index regardless of current status
index_all_sources()             — re-index every active source (startup / endpoint)
"""

import logging
import threading
from typing import Any

from app.db.source_repository import (
    get_source_by_id,
    get_sources_pending_indexing,
    get_db_query_for_source,
    update_index_status,
)
from app.db.vector_store import (
    deactivate_chunks_for_source,
    insert_chunks,
)
# NOTE: embedding_engine is imported lazily inside _run_pipeline() below,
# NOT at module level. This prevents the daemon thread from spawning when
# indexing_engine is first imported (e.g. when admin_page loads), which
# caused stdout interference and silent process exits on Windows.
from app.sources.document_loaders import (
    load_pdf, load_docx, load_txt, load_image,
    load_excel, load_url, load_database_query, database_rows_to_texts,
)
from app.sources.text_chunker import chunk_texts
from app.utils.properties_loader import props

logger = logging.getLogger(__name__)

# Lock so concurrent requests don't trigger duplicate indexing runs
_indexing_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_texts_for_source(source: dict[str, Any]) -> list[str]:
    """
    Dispatch to the correct document loader based on source type.

    The source dict must contain the key ``source_type`` (string) which is
    the value of RST_TYPE_NAME from RAG_SOURCE_TYPES, e.g. 'pdf', 'url'.

    All source_repository queries alias RST_TYPE_NAME as ``source_type`` so
    both get_source_by_id() and get_sources_pending_indexing() are consistent.

    Returns:
        List of raw text strings ready for chunking.
        Empty list if the source has no content or an unknown type.
    """
    # Use "source_type" key — all source_repository queries alias it consistently
    source_type = (source.get("source_type") or "").strip().lower()
    source_path = (source.get("source_path") or "").strip()
    source_id   = source["source_id"]
    extra_text  = (source.get("extra_text") or "").strip()

    texts: list[str] = []

    if source_type == "pdf":
        texts = load_pdf(source_path)

    elif source_type in ("docx", "doc"):
        texts = load_docx(source_path)

    elif source_type == "txt":
        texts = load_txt(source_path)

    elif source_type == "image":
        texts = load_image(source_path)

    elif source_type == "excel":
        texts = load_excel(source_path)

    elif source_type == "url":
        texts = load_url(source_path)

    elif source_type == "database":
        db_config = get_db_query_for_source(source_id)
        if db_config:
            rows  = load_database_query(
                db_config["query_sql"],
                db_config.get("role_column"),
            )
            texts = database_rows_to_texts(
                rows,
                role_column=db_config.get("role_column"),
            )
        else:
            logger.warning(
                "No DB query configured for source_id=%s — nothing to index.",
                source_id,
            )

    elif source_type == "":
        logger.error(
            "source_type is empty for source_id=%s. "
            "Check that RAG_SOURCE_TYPES.RST_TYPE_NAME is populated and "
            "the JOIN in get_sources_pending_indexing / get_source_by_id "
            "aliases it as 'source_type'.",
            source_id,
        )

    else:
        logger.warning(
            "Unknown source type '%s' for source_id=%s. "
            "Supported types: pdf, docx, doc, txt, image, excel, url, database.",
            source_type, source_id,
        )

    # Always append any manually-entered extra text
    if extra_text:
        texts.append(extra_text)

    return texts


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

def _run_pipeline(source: dict[str, Any]) -> None:
    """Execute the full index pipeline for a single source dict."""
    source_id = source["source_id"]
    source_name = source.get("source_name", str(source_id))

    logger.info("=== Indexing source [%d] '%s' ===", source_id, source_name)
    update_index_status(source_id, "indexing")

    try:
        # Step 1 — Load
        texts = _load_texts_for_source(source)
        if not texts:
            msg = "No text content extracted from source."
            logger.warning("Source [%d] '%s': %s", source_id, source_name, msg)
            update_index_status(source_id, "indexed")  # Mark done; nothing to store
            return

        # Step 2 — Chunk
        # Attach source metadata to every chunk so retrieval results can be
        # attributed back to their origin in the UI citation panel.
        metadata = {
            "source_id":   source_id,
            "source_name": source_name,
            "source_type": source.get("source_type", ""),
            "source_path": source.get("source_path", ""),
        }
        chunks = chunk_texts(texts, source_metadata=metadata)
        if not chunks:
            update_index_status(source_id, "indexed")
            return

        # Step 3 — Embed
        # Import lazily here so that importing indexing_engine at module level
        # (e.g. from admin_page) does NOT trigger the embedding daemon thread.
        from app.core.embedding_engine import embed_batch  # noqa: PLC0415

        # Guard: batch_size must be >= 1 to avoid ValueError from range()
        batch_size = props.get_int("indexing.batch_size", 50)
        if batch_size < 1:
            logger.warning(
                "indexing.batch_size is %d — invalid. Using default 50.", batch_size
            )
            batch_size = 50

        all_texts  = [c["content"] for c in chunks]
        embeddings: list[list[float]] = []

        # Process chunks in batches to avoid OOM on large sources
        for i in range(0, len(all_texts), batch_size):
            batch = all_texts[i : i + batch_size]
            embeddings.extend(embed_batch(batch))

        for i, chunk in enumerate(chunks):
            chunk["embedding"] = embeddings[i]

        # Step 4 — Deactivate old chunks
        deactivate_chunks_for_source(source_id)

        # Step 5 — Insert new chunks
        inserted = insert_chunks(source_id, chunks)

        logger.info(
            "Source [%d] '%s' indexed: %d chunks inserted.",
            source_id, source_name, inserted,
        )
        update_index_status(source_id, "indexed")

    except Exception as exc:
        error_msg = str(exc)[:500]
        logger.error("Indexing failed for source [%d] '%s': %s", source_id, source_name, exc)
        update_index_status(source_id, "failed", error=error_msg)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def index_source(source_id: int) -> tuple[bool, str]:
    """
    Index a single source by ID.

    Returns:
        (success, message)
    """
    source = get_source_by_id(source_id)
    if not source:
        return False, f"Source {source_id} not found."
    try:
        _run_pipeline(source)
        return True, "Indexing completed."
    except Exception as exc:
        return False, str(exc)


def index_all_pending() -> dict[str, int]:
    """
    Index all sources currently in 'pending' status.

    Returns:
        {"indexed": N, "failed": M}
    """
    pending = get_sources_pending_indexing()
    if not pending:
        logger.info("No pending sources to index.")
        return {"indexed": 0, "failed": 0}

    counts = {"indexed": 0, "failed": 0}
    for source in pending:
        try:
            _run_pipeline(source)
            counts["indexed"] += 1
        except Exception as exc:
            logger.error("Pipeline error for source %s: %s", source.get("source_id"), exc)
            counts["failed"] += 1

    logger.info("Pending indexing complete: %s", counts)
    return counts


def index_all_sources() -> dict[str, int]:
    """
    Re-index EVERY active source (used on startup or via the refresh endpoint).
    All sources are marked 'pending' first, then indexed sequentially.
    """
    with _indexing_lock:
        try:
            from app.db.db_manager import get_engine
            from sqlalchemy import text

            engine = get_engine()
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE RAG_RAG_SOURCES "
                        "SET RRS_INDEX_STATUS = 'pending' "
                        "WHERE RRS_isValid = 1"
                    )
                )
            logger.info("All active sources marked as pending for re-indexing.")
        except Exception as exc:
            logger.error("Failed to mark sources pending: %s", exc)
            return {"indexed": 0, "failed": 0}

        return index_all_pending()


def refresh_source_async(source_id: int) -> None:
    """
    Trigger indexing for a single source in a background thread.
    daemon=False ensures Python does not exit while this thread is running.
    The thread always finishes in bounded time via index_source().
    """
    def _safe_index():
        try:
            index_source(source_id)
        except Exception as exc:
            logger.error("refresh_source_async thread error source_id=%s: %s", source_id, exc)

    # FIX: daemon=True so this thread does not prevent Streamlit's script
    # runner from restarting cleanly on the next page interaction.
    thread = threading.Thread(
        target=_safe_index,
        daemon=True,
        name=f"IndexSource-{source_id}",
    )
    thread.start()
    logger.info("Background indexing started for source_id=%s", source_id)


_startup_done = False
_startup_lock = threading.Lock()


def startup_indexing() -> None:
    """
    Called once at application startup.
    Only runs if indexing.index_on_startup=true in app.properties.
    Runs in a background thread so Streamlit starts immediately.

    Uses daemon=False — a daemon thread causes silent process exit when
    it is the only thread running after Streamlit's script cycle completes.
    """
    global _startup_done
    with _startup_lock:
        if _startup_done:
            return
        _startup_done = True

    if not props.get_bool("indexing.index_on_startup"):
        logger.info("Startup indexing disabled via properties.")
        return

    def _bg():
        logger.info("--- Startup indexing begin ---")
        result = index_all_pending()
        logger.info("--- Startup indexing done: %s ---", result)

    # FIX: daemon=True — a daemon=False thread that outlives the Streamlit
    # script run causes the process to exit silently after all non-daemon
    # threads finish. daemon=True allows Streamlit to restart cleanly.
    threading.Thread(
        target=_bg,
        daemon=True,
        name="StartupIndexing",
    ).start()
