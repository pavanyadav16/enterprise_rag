"""
text_chunker.py
---------------
Purpose
-------
Splits raw text strings into overlapping fixed-size chunks that are small
enough to be individually embedded and stored in the vector store.

Why chunking?
-------------
Language models and embedding models have context-length limits.  A 50-page
PDF cannot be embedded as a single vector.  Breaking it into chunks of ~512
characters lets us:
  1. Embed each chunk individually.
  2. Retrieve only the most relevant 5–10 chunks for each user query.
  3. Keep the LLM prompt within its context window.

Overlap
-------
Adjacent chunks share ``indexing.chunk_overlap`` characters.  This prevents
a sentence that happens to fall on a chunk boundary from being split across
two chunks with neither containing it in full.

Primary implementation: LangChain RecursiveCharacterTextSplitter
  Splits at paragraph breaks first, then sentences, then words, then
  characters — preserving natural language boundaries where possible.

Fallback: _naive_chunk()
  Used when LangChain is not installed.  Simple fixed-size slicing with
  overlap — no boundary awareness.

Configuration (conf/app.properties)
------------------------------------
  indexing.chunk_size    — target character count per chunk  (default 512)
  indexing.chunk_overlap — overlap characters between chunks (default 64)
"""

import logging
from typing import Any

from app.utils.properties_loader import props

logger = logging.getLogger(__name__)

# Safe minimum values that prevent downstream crashes.
# chunk_size  must be > 0 for LangChain and the naive chunker.
# chunk_overlap must be >= 0 and < chunk_size.
_DEFAULT_CHUNK_SIZE    = 512
_DEFAULT_CHUNK_OVERLAP = 64


def _safe_chunk_params() -> tuple[int, int]:
    """
    Read chunk_size and chunk_overlap from properties with safety guards.

    Returns:
        (chunk_size, chunk_overlap) — both guaranteed to be valid positive
        integers with overlap < size.

    Guards applied:
      - chunk_size  < 1 → replaced with _DEFAULT_CHUNK_SIZE
      - chunk_overlap < 0 → replaced with 0
      - chunk_overlap >= chunk_size → clamped to chunk_size // 4
    """
    size    = props.get_int("indexing.chunk_size",    _DEFAULT_CHUNK_SIZE)
    overlap = props.get_int("indexing.chunk_overlap", _DEFAULT_CHUNK_OVERLAP)

    # chunk_size must be at least 1 character
    if size < 1:
        logger.warning(
            "indexing.chunk_size is %d — invalid.  Using default %d.",
            size, _DEFAULT_CHUNK_SIZE,
        )
        size = _DEFAULT_CHUNK_SIZE

    # overlap must be non-negative and strictly less than size
    if overlap < 0:
        overlap = 0
    elif overlap >= size:
        clamped = size // 4
        logger.warning(
            "indexing.chunk_overlap (%d) >= chunk_size (%d) — clamping to %d.",
            overlap, size, clamped,
        )
        overlap = clamped

    return size, overlap


def chunk_texts(
    texts: list[str],
    source_metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Split a list of raw text strings into overlapping chunks ready for embedding.

    Each input string is split independently.  The resulting chunks are
    numbered sequentially across all input strings (chunk_index is global,
    not per-input-string).

    Args:
        texts:           Raw text segments produced by a document loader.
                         Empty strings and whitespace-only strings are skipped.
        source_metadata: Arbitrary dict attached to every chunk produced from
                         these texts.  Typically contains source_id,
                         source_name, and source_type so retrieval results
                         can be attributed back to their origin.

    Returns:
        List of chunk dicts, each containing:
          - chunk_index (int)  : 0-based position across all chunks
          - content     (str)  : the chunk text
          - metadata    (dict) : copy of source_metadata
        Returns an empty list if all input texts are empty.
    """
    chunk_size, chunk_overlap = _safe_chunk_params()
    meta = source_metadata or {}

    # Attempt to use LangChain's smart splitter (respects paragraph/sentence
    # boundaries).  Fall back gracefully if LangChain is not installed.
    splitter = None
    try:
        from langchain.text_splitter import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            # Separator priority: paragraph break → newline → sentence → word → char
            separators=["\n\n", "\n", ". ", " ", ""],
        )
    except ImportError:
        logger.warning(
            "langchain not installed — using naive fixed-size chunker. "
            "Install langchain for better chunking quality."
        )
    except Exception as exc:
        logger.error("Failed to create LangChain splitter: %s — using naive chunker.", exc)

    chunks: list[dict[str, Any]] = []

    for text in texts:
        # Skip empty or whitespace-only strings
        if not text or not text.strip():
            continue

        # Split using whichever splitter is available
        if splitter is not None:
            try:
                segments = splitter.split_text(text)
            except Exception as exc:
                logger.error("LangChain split_text failed: %s — falling back to naive.", exc)
                segments = _naive_chunk(text, chunk_size, chunk_overlap)
        else:
            segments = _naive_chunk(text, chunk_size, chunk_overlap)

        # Build chunk dicts; skip empty segments produced by the splitter
        for seg in segments:
            seg = seg.strip()
            if seg:
                chunks.append(
                    {
                        "chunk_index": len(chunks),  # global sequential index
                        "content":     seg,
                        "metadata":    meta,
                    }
                )

    logger.debug(
        "chunk_texts: produced %d chunks from %d text segment(s) "
        "(chunk_size=%d overlap=%d).",
        len(chunks), len(texts), chunk_size, chunk_overlap,
    )
    return chunks


def _naive_chunk(text: str, size: int, overlap: int) -> list[str]:
    """
    Simple fixed-size character chunker used as a fallback when LangChain
    is unavailable or raises an error.

    Does not respect word or sentence boundaries — a word may be split
    across two chunks.  Use only when LangChain is not installed.

    Args:
        text:    Input text string.
        size:    Maximum characters per chunk.  Must be >= 1.
        overlap: Number of characters shared between adjacent chunks.
                 Must be >= 0 and < size.

    Returns:
        List of text slices.
    """
    results: list[str] = []
    start = 0
    step  = size - overlap  # guaranteed > 0 by _safe_chunk_params()

    while start < len(text):
        results.append(text[start : start + size])
        start += step

    return results
