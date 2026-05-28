"""
properties_loader.py
--------------------
Purpose
-------
Loads application configuration from conf/app.properties and exposes a
typed, cached singleton called ``props``.

Usage example
-------------
    from app.utils.properties_loader import props

    host = props.get("db.host")          # returns str or None
    port = props.get_int("db.port")      # returns int (0 if missing/unparseable)
    debug = props.get_bool("app.dev_mode")   # returns True/False

Priority (highest → lowest)
----------------------------
  1. Operating system environment variable  (e.g. DB_PASSWORD)
  2. Value from conf/app.properties file
  3. The ``default`` argument passed to the get*() call (if any)
  4. The method's own built-in fallback (0, False, [] etc.)

Environment variable naming rule
---------------------------------
  property key  → uppercase + replace dots with underscores
  db.password   → DB_PASSWORD
  app.dev_mode  → APP_DEV_MODE

Thread safety
-------------
The singleton is created once at module import time and is read-only
after that.  All subsequent imports return the same cached object from
sys.modules — no locking required.
"""

import os
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Resolve conf/ relative to THIS file's location so the path is correct
# regardless of the current working directory when the app starts.
_CONF_DIR          = Path(__file__).resolve().parent.parent.parent / "conf"
_DEFAULT_PROPS_FILE = _CONF_DIR / "app.properties"


class AppProperties:
    """
    Read-only, thread-safe singleton that wraps conf/app.properties.

    Do not instantiate this class directly — use the module-level
    ``props`` singleton defined at the bottom of this file.
    """

    # Class-level fields shared across all (the one) instance
    _instance: Optional["AppProperties"] = None
    _data: dict[str, str] = {}  # raw string values from the properties file

    def __new__(cls, props_file: Optional[Path] = None) -> "AppProperties":
        """
        Ensure only one instance is ever created (Singleton pattern).

        The instance is created on the first import and returned as-is
        on every subsequent import without re-loading the file.
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load(props_file or _DEFAULT_PROPS_FILE)
        return cls._instance

    # ------------------------------------------------------------------
    # Private: file parsing
    # ------------------------------------------------------------------

    def _load(self, props_file: Path) -> None:
        """
        Parse a Java-style .properties file into the internal dict.

        File format rules:
          - Lines starting with # are comments and are ignored.
          - Blank lines are ignored.
          - Each data line must be in the form  key=value
          - The first '=' separates key from value; extra '=' in the
            value are preserved as-is.
          - Leading/trailing whitespace around both key and value is stripped.

        Args:
            props_file: Path to the .properties file to load.
        """
        if not props_file.exists():
            logger.warning(
                "Properties file not found at '%s'. "
                "All values will fall back to code defaults or environment variables.",
                props_file,
            )
            return

        count = 0
        with open(props_file, encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                line = line.strip()

                # Skip blank lines and comment lines
                if not line or line.startswith("#"):
                    continue

                if "=" not in line:
                    logger.debug(
                        "Skipping malformed line %d in %s (no '=' found): %s",
                        line_no, props_file.name, line,
                    )
                    continue

                # partition splits on the FIRST '=' only
                key, _, value = line.partition("=")
                key   = key.strip()
                value = value.strip()

                if key:
                    self._data[key] = value
                    count += 1

        logger.info(
            "Loaded %d properties from %s",
            count, props_file,
        )

    # ------------------------------------------------------------------
    # Public typed accessors
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> str | None:
        """
        Return the string value for *key*.

        Lookup order:
          1. Environment variable (key uppercased, dots → underscores)
          2. Value from the properties file
          3. *default* argument (None if not supplied)

        Args:
            key:     Dot-notation property key e.g. 'db.host'.
            default: Value to return when the key is not found anywhere.

        Returns:
            String value, or *default* (which may be None).
        """
        # Convert 'db.host' → 'DB_HOST' for the environment variable lookup
        env_key = key.upper().replace(".", "_")
        return os.environ.get(env_key, self._data.get(key, default))

    def get_int(self, key: str, default: int = 0) -> int:
        """
        Return the value for *key* as an integer.

        Returns *default* (0 by default) if the key is missing or if the
        value cannot be converted to an integer.

        Args:
            key:     Dot-notation property key.
            default: Fallback integer value.

        Returns:
            Integer value of the property, or *default*.
        """
        raw = self.get(key)
        if raw is None:
            return default
        try:
            return int(raw)
        except (TypeError, ValueError):
            logger.warning(
                "Property '%s' has non-integer value '%s'; using default %d.",
                key, raw, default,
            )
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        """
        Return the value for *key* as a float.

        Returns *default* if the key is missing or unparseable.

        Args:
            key:     Dot-notation property key.
            default: Fallback float value.

        Returns:
            Float value of the property, or *default*.
        """
        raw = self.get(key)
        if raw is None:
            return default
        try:
            return float(raw)
        except (TypeError, ValueError):
            logger.warning(
                "Property '%s' has non-float value '%s'; using default %f.",
                key, raw, default,
            )
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        """
        Return the value for *key* as a boolean.

        Truthy string values: 'true', '1', 'yes', 'on'  (case-insensitive).
        Everything else (including missing keys) is treated as False.

        Args:
            key:     Dot-notation property key.
            default: Fallback boolean value.

        Returns:
            True or False.
        """
        raw = self.get(key)
        if raw is None:
            return default
        return raw.strip().lower() in ("true", "1", "yes", "on")

    def get_list(
        self,
        key: str,
        separator: str = ",",
        default: list | None = None,
    ) -> list[str]:
        """
        Return the value for *key* as a list of trimmed strings.

        Example: 'pdf,docx, txt' → ['pdf', 'docx', 'txt']

        Args:
            key:       Dot-notation property key.
            separator: Delimiter used to split the value string.
            default:   Fallback list returned when the key is missing.

        Returns:
            List of non-empty trimmed strings, or *default* ([] if not supplied).
        """
        raw = self.get(key)
        if raw is None:
            return default if default is not None else []
        return [v.strip() for v in raw.split(separator) if v.strip()]


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
# Import this object anywhere in the project:
#     from app.utils.properties_loader import props
#
# Python's module cache (sys.modules) guarantees that _load() is called
# exactly once per process — no matter how many files import this module.
# ---------------------------------------------------------------------------
props = AppProperties()
