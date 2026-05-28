"""
model_server.py
---------------
Standalone FastAPI service that loads the SentenceTransformer model and
serves embedding requests over HTTP.

MODEL LOADING
-------------
The model is downloaded automatically from HuggingFace Hub on first startup
and cached inside the container at /models/cache.
No volume mounts or manual model loading required.

Default model : sentence-transformers/all-MiniLM-L6-v2
Override via  : EMBEDDING_MODEL_NAME environment variable
                or embedding.model_name in conf/app.properties

USAGE (Docker Compose manages this automatically)
-------------------------------------------------
The model-server container starts before the backend container.
On first boot the model downloads (~90 MB). Subsequent starts are instant.
"""

import os
import sys
import logging
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.utils.logger_config import setup_logging
setup_logging()

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import time

from app.utils.properties_loader import props

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan: load model on startup, clean up on shutdown."""
    await _load_model_async()
    yield
    global _model
    _model = None
    logger.info("Embedding model server shut down cleanly.")

app = FastAPI(
    title="Enterprise RAG — Embedding Model Server",
    description="Serves sentence-transformer embeddings as a standalone HTTP service.",
    version=props.get("app.version", "2.0.8"),
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Model state
# ---------------------------------------------------------------------------
_model       = None
_load_error  = None
_loaded_at   = None


async def _load_model_async():
    """
    Load the SentenceTransformer model at startup.

    Priority:
      1. Local cache directory /models/cache  (fast — already downloaded)
      2. Download from HuggingFace Hub        (first boot only, ~90 MB)

    The model name is read from:
      - Environment variable EMBEDDING_MODEL_NAME
      - conf/app.properties key: embedding.model_name
      - Default: sentence-transformers/all-MiniLM-L6-v2
    """
    global _model, _load_error, _loaded_at

    # Priority: env var EMBEDDING_MODEL_NAME > app.properties > default
    model_name = (
        os.environ.get("EMBEDDING_MODEL_NAME")
        or props.get("embedding.model_name")
        or "sentence-transformers/all-MiniLM-L6-v2"
    ).strip()

    device = (props.get("embedding.device") or "cpu").strip()

    # Cache directory — must be writable by the container user.
    # HF_HOME env var is set in Dockerfile/compose to point here.
    cache_dir = Path(os.environ.get("HF_HOME", "/models/cache"))
    cache_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Loading embedding model: %s (device=%s)", model_name, device)
    logger.info("Cache directory: %s", cache_dir)
    t0 = time.time()

    try:
        # Suppress noisy transformers warnings
        try:
            import transformers
            transformers.logging.set_verbosity_error()
        except Exception:
            pass

        from sentence_transformers import SentenceTransformer

        # SentenceTransformer will use the cache_folder if provided.
        # If the model is already cached it loads instantly.
        # If not, it downloads from HuggingFace Hub automatically.
        _model = SentenceTransformer(
            model_name,
            device=device,
            cache_folder=str(cache_dir),
        )
        _loaded_at = time.time()

        logger.info(
            "Embedding model ready. model=%s dim=%d load_time=%.1fs",
            model_name,
            _model.get_embedding_dimension(),
            _loaded_at - t0,
        )
    except Exception as exc:
        _load_error = str(exc)
        logger.error("Failed to load embedding model '%s': %s", model_name, exc)


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class EmbedRequest(BaseModel):
    texts: list[str]

class EmbedResponse(BaseModel):
    embeddings: list[list[float]]
    dimension: int
    count: int

class StatusResponse(BaseModel):
    ready: bool
    error: Optional[str] = None
    dimension: Optional[int] = None
    model_name: str
    device: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok" if _model is not None else "loading",
        "ready":  _model is not None,
    }


@app.get("/status", response_model=StatusResponse)
def status():
    model_name = (
        props.get("embedding.model_name")
        or "sentence-transformers/all-MiniLM-L6-v2"
    )
    return StatusResponse(
        ready     = _model is not None,
        error     = _load_error,
        dimension = _model.get_embedding_dimension() if _model else None,
        model_name= model_name,
        device    = props.get("embedding.device", "cpu"),
    )


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest):
    if _model is None:
        raise HTTPException(
            status_code=503,
            detail=_load_error or "Model not loaded yet. Please wait and retry.",
        )
    if not req.texts:
        return EmbedResponse(embeddings=[], dimension=0, count=0)

    try:
        batch_size = props.get_int("embedding.batch_size", 32)
        if batch_size < 1:
            batch_size = 32

        vectors = _model.encode(
            req.texts,
            batch_size=batch_size,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return EmbedResponse(
            embeddings = [v.tolist() for v in vectors],
            dimension  = _model.get_embedding_dimension(),
            count      = len(vectors),
        )
    except Exception as exc:
        logger.error("Embed failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Embedding failed: {exc}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    host = props.get("model_server.host", "0.0.0.0")
    port = props.get_int("model_server.port", 8502)
    logger.info("Starting model server on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port, reload=False)
