"""
logger_config.py
----------------
Centralised logging setup for the Enterprise RAG application.

- Console handler  → DEBUG-level messages (developer detail)
- File handler     → INFO-level messages (production audit trail)

Call setup_logging() once at application start-up.
"""

import logging
import sys
import warnings
from pathlib import Path

from app.utils.properties_loader import props


def setup_logging() -> None:
    """Configure root logger with console and rotating file handlers."""
    log_level_name = (props.get("app.log_level") or "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    raw_log_path = props.get("app.log_file") or "logs/app.log"
    log_file = Path(raw_log_path).resolve()
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        print(
            f"[WARN] Could not create log directory '{log_file.parent}': {exc}",
            file=sys.stderr,
        )

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # capture everything; handlers filter

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # --- Console handler (DEBUG so developers see full trace) ---
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG)
    console.setFormatter(fmt)

    # --- File handler (INFO for production-grade logs) ---
    try:
        from logging.handlers import RotatingFileHandler

        file_handler = RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] Could not create file log handler: {exc}", file=sys.stderr)

    root.addHandler(console)

    # ---------------------------------------------------------------------------
    # Silence overly verbose third-party loggers
    # ---------------------------------------------------------------------------
    noisy_loggers = (
        "httpx",
        "httpcore",
        "urllib3",
        "PIL",
        "sentence_transformers",
        "transformers",               # __path__ alias + sharding messages
        "transformers.modeling_utils",
        "transformers.models",
        "torch",
        "torchvision",
    )
    for name in noisy_loggers:
        logging.getLogger(name).setLevel(logging.ERROR)

    # ---------------------------------------------------------------------------
    # Suppress Python warnings from third-party packages
    # ---------------------------------------------------------------------------

    # transformers: "[transformers] Accessing `__path__` from ..." — printed for
    # every image-processing model module scanned at import time.
    warnings.filterwarnings(
        "ignore",
        message=r".*Accessing `__path__`.*",
    )

    # transformers: "The following layers were not sharded" — harmless info
    # message printed when loading a small model saved without tensor sharding.
    warnings.filterwarnings(
        "ignore",
        message=r".*layers were not sharded.*",
    )

    # transformers / HuggingFace Hub: deprecated argument warnings
    warnings.filterwarnings(
        "ignore",
        message=r".*resume_download.*",
        category=FutureWarning,
    )
    warnings.filterwarnings(
        "ignore",
        message=r".*use_auth_token.*",
        category=FutureWarning,
    )

    # Broad catch-all for any remaining FutureWarning from transformers or torch
    warnings.filterwarnings(
        "ignore",
        category=FutureWarning,
        module=r"transformers.*",
    )
    warnings.filterwarnings(
        "ignore",
        category=UserWarning,
        module=r"torch.*",
    )

    # Streamlit: suppress any remaining width/container deprecation warnings
    warnings.filterwarnings("ignore", message=r".*width.*stretch.*")

    logging.getLogger(__name__).info("Logging initialised at level %s", log_level_name)
