"""
embedding_engine.py
-------------------
HTTP client for the standalone model_server.py embedding service.

The SentenceTransformer model runs in its own container (model-server).
This module is a thin HTTP client — no threads, no model loading, no
background workers. The FastAPI backend stays completely stateless.

STARTUP ORDER (Docker Compose handles this automatically)
---------------------------------------------------------
1. model-server container starts and loads the model
2. backend container starts and connects to model-server

CONFIGURATION (conf/app.properties)
-------------------------------------
    model_server.host = model-server   (Docker service name)
    model_server.port = 8502

FALLBACK
--------
If the model server is unreachable, every function raises RuntimeError
with a clear message. The RAG pipeline catches this and returns a
graceful error response — the FastAPI process never exits.
"""

import logging
import requests

from app.utils.properties_loader import props

logger = logging.getLogger(__name__)


def _base_url() -> str:
    host = (props.get("model_server.host") or "localhost").strip()
    port = props.get_int("model_server.port", 8502)
    return f"http://{host}:{port}"


def _timeout() -> int:
    """Request timeout in seconds — generous for large batches."""
    return props.get_int("llm.request_timeout_seconds", 120) or 120


# ---------------------------------------------------------------------------
# Public API — mirrors the original interface exactly so all callers work
# without any changes.
# ---------------------------------------------------------------------------

def embed_text(text: str) -> list[float]:
    """
    Embed a single string.
    Calls POST /embed on the model server and returns the vector.
    """
    results = embed_batch([text])
    return results[0] if results else []


def embed_batch(texts: list[str]) -> list[list[float]]:
    """
    Embed a list of strings.
    Calls POST /embed on the model server.
    """
    if not texts:
        return []

    url = f"{_base_url()}/embed"
    try:
        resp = requests.post(
            url,
            json={"texts": texts},
            timeout=_timeout(),
        )
        resp.raise_for_status()
        data = resp.json()
        return data["embeddings"]
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"Cannot connect to the embedding model server at {_base_url()}. "
            "Start it with: python model_server.py"
        )
    except requests.exceptions.Timeout:
        raise RuntimeError(
            f"Embedding model server timed out after {_timeout()}s. "
            "The batch may be too large or the server is overloaded."
        )
    except requests.exceptions.HTTPError as exc:
        raise RuntimeError(
            f"Embedding model server returned HTTP {exc.response.status_code}: "
            f"{exc.response.text[:200]}"
        )
    except Exception as exc:
        raise RuntimeError(f"Embedding request failed: {exc}") from exc


def is_ready() -> bool:
    """
    Non-blocking check: returns True if the model server is up and the
    model is loaded. Safe to call from the Streamlit script runner thread.
    """
    try:
        resp = requests.get(f"{_base_url()}/health", timeout=3)
        return resp.ok and resp.json().get("ready", False)
    except Exception:
        return False


def status() -> dict:
    """
    Return a status snapshot from the model server.
    Returns a safe default dict if the server is unreachable.
    """
    try:
        resp = requests.get(f"{_base_url()}/status", timeout=3)
        if resp.ok:
            return resp.json()
    except Exception:
        pass
    return {
        "ready": False,
        "error": f"Model server unreachable at {_base_url()}",
        "dimension": None,
    }


def get_embedding_dimension() -> int:
    """Return the embedding dimension from the model server or config fallback."""
    try:
        s = status()
        if s.get("dimension"):
            return s["dimension"]
    except Exception:
        pass
    return props.get_int("embedding.dimension", 384)
