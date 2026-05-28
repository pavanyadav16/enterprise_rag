"""
rag_pipeline.py
---------------
Core Retrieval-Augmented Generation pipeline.

Steps:
  1. Embed the user query.
  2. Retrieve top-K relevant chunks from the vector store
     (scoped to sources the user is authorised to access).
  3. Build a context-aware prompt.
  4. Call the LLM and return the response.
  5. If no relevant content is found, return a helpful fallback
     listing the topics the user *can* ask about.

Also handles in-session uploaded file content (stored in memory,
not persisted to the vector store).
"""

import logging
from typing import Any

# embedding_engine imported lazily inside answer_query() — see note there
from app.db.vector_store import similarity_search
from app.db.user_repository import get_accessible_source_ids, get_accessible_sources_with_names
from app.llm.llm_client import generate_response
from app.utils.properties_loader import props

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_CONTEXT_SYSTEM_PROMPT = """You are a strict enterprise knowledge assistant.
Answer ONLY using the provided context below.
If the context does not contain enough information to answer the question, say so clearly.
Do NOT make up facts. Do NOT reference information outside the provided context.
Be concise, professional, and accurate."""

_NO_CONTEXT_RESPONSE_TEMPLATE = """I could not find any relevant information in the knowledge base for your question.

Here are the topics I can help you with based on your access:
{topic_list}

Please try rephrasing your question or ask about one of the topics above."""

_NO_ACCESS_RESPONSE = (
    "You do not have access to any knowledge sources. "
    "Please contact your administrator to be assigned the appropriate role."
)


# ---------------------------------------------------------------------------
# Context retrieval
# ---------------------------------------------------------------------------

def _retrieve_context(
    query_embedding: list[float],
    source_ids: list[int],
) -> list[dict[str, Any]]:
    """
    Query the vector store for the top-K chunks most similar to the query.

    Reads retrieval parameters from app.properties with safe fallbacks:
      rag.top_k_results        — max chunks to return       (default 5)
      rag.similarity_threshold — min cosine similarity 0–1  (default 0.35)

    Args:
        query_embedding: Float vector produced by the embedding model.
        source_ids:      IDs of sources the user is authorised to search.

    Returns:
        List of chunk dicts ordered by descending similarity score.
    """
    # Guard: top_k must be >= 1 otherwise no results are ever returned
    top_k = props.get_int("rag.top_k_results", 5)
    if top_k < 1:
        logger.warning("rag.top_k_results is %d — invalid. Using default 5.", top_k)
        top_k = 5

    # Guard: threshold must be in [0.0, 1.0]
    threshold = props.get_float("rag.similarity_threshold", 0.35)
    if not (0.0 <= threshold <= 1.0):
        logger.warning(
            "rag.similarity_threshold is %.2f — out of range [0,1]. Using 0.35.",
            threshold,
        )
        threshold = 0.35

    return similarity_search(
        query_embedding=query_embedding,
        source_ids=source_ids,
        top_k=top_k,
        threshold=threshold,
    )


def _build_prompt_with_context(
    user_query: str,
    context_chunks: list[dict[str, Any]],
    uploaded_texts: list[str] | None = None,
) -> str:
    """
    Assemble a user prompt that includes the retrieved context.

    Args:
        user_query:     The original user question.
        context_chunks: Retrieved chunks from the vector store.
        uploaded_texts: Any text extracted from a file uploaded during chat.

    Returns:
        Formatted prompt string.
    """
    # Guard: max_context_tokens must be > 0 otherwise the context is always truncated to nothing
    max_tokens = props.get_int("rag.max_context_tokens", 3000)
    if max_tokens < 1:
        logger.warning(
            "rag.max_context_tokens is %d — invalid. Using default 3000.", max_tokens
        )
        max_tokens = 3000

    sections: list[str] = []

    # Include uploaded file content first (higher priority)
    if uploaded_texts:
        upload_block = "\n\n".join(uploaded_texts)
        sections.append(f"[Uploaded File Content]\n{upload_block}")

    # Add retrieved vector-store chunks
    for i, chunk in enumerate(context_chunks, 1):
        source_name = chunk.get("metadata", {}).get("source_name", "Unknown")
        sections.append(f"[Source {i}: {source_name}]\n{chunk['content']}")

    context_str = "\n\n---\n\n".join(sections)

    # Truncate to avoid exceeding the LLM's context window
    if len(context_str) > max_tokens * 4:  # ~4 chars per token
        context_str = context_str[: max_tokens * 4] + "\n\n[Context truncated]"

    return (
        f"Context:\n{context_str}\n\n"
        f"---\n\n"
        f"Question: {user_query}"
    )


def _build_topic_list(user_id: int) -> str:
    """Build a bullet list of accessible source names for fallback response."""
    sources = get_accessible_sources_with_names(user_id)
    if not sources:
        return "  (no accessible sources found)"
    return "\n".join(
        f"  • {s['source_name']}"
        + (f" — {s['description']}" if s.get("description") else "")
        for s in sources
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def answer_query(
    user_query: str,
    user_id: int,
    uploaded_file_texts: list[str] | None = None,
) -> dict[str, Any]:
    """
    Run the full RAG pipeline for a user query.

    Args:
        user_query:          The user's question.
        user_id:             ID of the authenticated user (for RBAC).
        uploaded_file_texts: Optional text extracted from a chat-time upload.

    Returns:
        dict with keys:
            response  (str)  — the assistant's answer
            sources   (list) — chunks used (for citation/debug)
            has_context (bool)
            error     (str | None)
    """
    if not user_query.strip():
        return {
            "response": "Please enter a question.",
            "sources": [],
            "has_context": False,
            "error": None,
        }

    # Step 1 — Get accessible sources
    source_ids = get_accessible_source_ids(user_id)

    # Allow upload-only queries even without vector-store sources
    has_upload = bool(uploaded_file_texts)

    if not source_ids and not has_upload:
        return {
            "response": _NO_ACCESS_RESPONSE,
            "sources": [],
            "has_context": False,
            "error": None,
        }

    # Step 2 — Embed query
    # Import lazily so that importing rag_pipeline does not trigger the
    # embedding daemon thread before the app is fully initialised.
    try:
        from app.core.embedding_engine import embed_text  # noqa: PLC0415
        query_embedding = embed_text(user_query)
    except RuntimeError as exc:
        logger.error("Embedding failed: %s", exc)
        return {
            "response": "An error occurred while processing your query. Please try again.",
            "sources": [],
            "has_context": False,
            "error": str(exc),
        }

    # Step 3 — Retrieve context
    context_chunks: list[dict[str, Any]] = []
    if source_ids:
        try:
            context_chunks = _retrieve_context(query_embedding, source_ids)
        except Exception as exc:
            logger.error("Vector retrieval failed: %s", exc)
            # Non-fatal — continue with upload content if available

    # Step 3b — Enrich chunks with source_path and source_type from SQL Server.
    # This ensures the UI can always show URL / filename even for chunks indexed
    # before source_path was added to the JSONB metadata.
    if context_chunks:
        try:
            from app.db.source_repository import get_source_by_id as _get_src
            _src_cache: dict[int, dict] = {}
            for chunk in context_chunks:
                sid = chunk.get("source_id")
                if sid is None:
                    sid = (chunk.get("metadata") or {}).get("source_id")
                if sid is None:
                    continue
                if sid not in _src_cache:
                    rec = _get_src(int(sid))
                    _src_cache[sid] = rec or {}
                rec = _src_cache[sid]
                if not isinstance(chunk.get("metadata"), dict):
                    chunk["metadata"] = {}
                # Always overwrite with authoritative SQL Server values
                if rec.get("source_path"):
                    chunk["metadata"]["source_path"] = rec["source_path"]
                if rec.get("source_type"):
                    chunk["metadata"]["source_type"] = rec["source_type"]
                if rec.get("source_name"):
                    chunk["metadata"]["source_name"] = rec["source_name"]
        except Exception as _enrich_exc:
            logger.warning("Could not enrich chunk metadata: %s", _enrich_exc)

    # Step 4 — Handle no-context case
    has_context = bool(context_chunks) or has_upload
    if not has_context:
        topic_list = _build_topic_list(user_id)
        return {
            "response": _NO_CONTEXT_RESPONSE_TEMPLATE.format(topic_list=topic_list),
            "sources": [],
            "has_context": False,
            "error": None,
        }

    # Step 5 — Build prompt and call LLM
    user_prompt = _build_prompt_with_context(
        user_query, context_chunks, uploaded_file_texts
    )

    try:
        llm_response = generate_response(
            user_prompt=user_prompt,
            system_prompt=_CONTEXT_SYSTEM_PROMPT,
        )
    except RuntimeError as exc:
        logger.error("LLM call failed: %s", exc)
        return {
            "response": (
                "I'm sorry, I was unable to generate a response at this time. "
                "Please try again later or contact support."
            ),
            "sources": context_chunks,
            "has_context": True,
            "error": str(exc),
        }

    logger.info(
        "Query answered for user_id=%d using %d chunks + upload=%s",
        user_id, len(context_chunks), has_upload,
    )

    return {
        "response": llm_response,
        "sources": context_chunks,
        "has_context": True,
        "error": None,
    }
