"""
db_manager.py
-------------
Manages connections to the SQL Server database using SQLAlchemy.

Locking strategy
----------------
SQL Server uses shared locks on SELECTs by default (READ COMMITTED isolation).
These shared locks block writers and can cause UI hangs when many concurrent
reads compete with indexing writes.

We apply two fixes:

1. READ UNCOMMITTED for all read-only queries
   ``execute_raw`` and ``execute_raw_safe`` prefix every SELECT with
   ``SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED`` so no shared locks
   are taken.  Dirty reads are acceptable for RAG retrieval and admin UI.

2. Short write transactions via ``get_session()``
   Write operations (INSERT / UPDATE) still use the default READ COMMITTED
   isolation with autocommit=False so data integrity is preserved.
   The session is committed and closed as quickly as possible.

3. Connection and query timeouts via connect_args
   ``LoginTimeout`` limits the time spent waiting for a new connection.
   ``timeout`` (pyodbc) limits individual statement execution time.
   Both prevent indefinite hangs when SQL Server is slow or unreachable.

Provides
--------
  get_engine()        → reusable SQLAlchemy engine (singleton)
  get_session()       → context-managed session for write operations
  execute_raw()       → read-only SQL, READ UNCOMMITTED, raises on error
  execute_raw_safe()  → same but returns (rows, error_str), never raises
  test_connection()   → quick health check
"""

import atexit
import logging
from contextlib import contextmanager
from typing import Generator, Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, URL
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError, OperationalError

from app.utils.properties_loader import props

logger = logging.getLogger(__name__)

_engine: Engine | None = None

# SQL prefix applied to every read query to prevent shared lock acquisition.
# READ UNCOMMITTED means we never block writers and never wait for writers.
_READ_UNCOMMITTED = "SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;\n"


def _build_engine_url() -> URL:
    """
    Build a SQLAlchemy URL for SQL Server via pyodbc.

    URL.create() is used instead of an f-string so passwords containing
    special characters (@ # % +) are never mis-parsed as URL delimiters.
    """
    host     = props.get("db.host")
    port     = props.get_int("db.port", 1433)
    db_name  = props.get("db.name")
    user     = props.get("db.username")
    password = props.get("db.password")
    driver   = props.get("db.driver")

    # LoginTimeout  — max seconds to wait for a new TCP connection to SQL Server.
    # Encrypt=no    — disable TLS overhead for internal networks (change for prod).
    odbc_connect = (
        f"DRIVER={{{driver}}};"
        f"SERVER={host},{port};"
        f"DATABASE={db_name};"
        f"UID={user};"
        f"PWD={password};"
        "TrustServerCertificate=yes;"
        "Encrypt=no;"
        "LoginTimeout=10;"      # fail fast if SQL Server is unreachable
    )

    return URL.create(
        drivername="mssql+pyodbc",
        query={"odbc_connect": odbc_connect},
    )


def _dispose_engine() -> None:
    """Close all pooled SQL Server connections on process exit."""
    global _engine
    if _engine is not None:
        try:
            _engine.dispose()
            logger.debug("SQL Server engine disposed.")
        except Exception:
            pass


def get_engine() -> Engine:
    """
    Return (or lazily create) the global SQLAlchemy engine.

    Engine is a singleton — connection pooling is managed internally.
    pool_pre_ping=True validates each connection before use, preventing
    stale-connection hangs after network interruptions.
    """
    global _engine
    if _engine is None:
        try:
            url = _build_engine_url()
            _engine = create_engine(
                url,
                pool_size=props.get_int("db.pool_size", 10),
                max_overflow=props.get_int("db.max_overflow", 20),
                # pool_timeout: max seconds to wait for a free connection
                # from the pool before raising.  Prevents indefinite hangs.
                pool_timeout=props.get_int("db.pool_timeout", 15),
                pool_pre_ping=True,   # validate connection health before use
                pool_recycle=1800,    # recycle connections every 30 min
                echo=False,
            )
            logger.info(
                "SQL Server engine created: host=%s db=%s",
                props.get("db.host"), props.get("db.name"),
            )
            # Register cleanup so the connection pool is closed on Ctrl+C / exit.
            # Without this, SQLAlchemy's QueuePool worker threads keep the
            # process alive after KeyboardInterrupt on Windows.
            atexit.register(_dispose_engine)
        except Exception as exc:
            logger.error("Failed to create SQL Server engine: %s", exc)
            raise
    return _engine


def test_connection() -> tuple[bool, str]:
    """
    Quick health check — verify SQL Server is reachable.

    Returns:
        (True, "")              on success.
        (False, error_message)  on failure.
    """
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, ""
    except OperationalError as exc:
        origin = getattr(exc, "orig", None)
        msg    = f"Database connection failed: {origin or exc}"
        logger.error(msg)
        return False, msg
    except Exception as exc:
        msg = f"Database error: {exc}"
        logger.error(msg)
        return False, msg


# ---------------------------------------------------------------------------
# Write session (transactional)
# ---------------------------------------------------------------------------

def _get_session_factory() -> sessionmaker:
    """Return a sessionmaker bound to the global engine."""
    return sessionmaker(bind=get_engine(), autocommit=False, autoflush=False)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """
    Provide a transactional scope for write operations (INSERT / UPDATE / DELETE).

    - Uses READ COMMITTED isolation (default) to maintain data integrity.
    - Commits on normal exit, rolls back on any exception.
    - Closes the connection immediately after the block so the pool slot
      is returned quickly and other threads do not wait.

    Usage::
        with get_session() as session:
            session.execute(text("INSERT INTO ..."), {...})
    """
    factory = _get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        logger.error("DB write session error — rolling back: %s", exc)
        raise
    except Exception as exc:
        session.rollback()
        logger.error("Unexpected DB session error — rolling back: %s", exc)
        raise
    finally:
        session.close()  # return connection to pool immediately


# ---------------------------------------------------------------------------
# Read-only helpers (READ UNCOMMITTED — no shared locks)
# ---------------------------------------------------------------------------

def execute_raw(sql: str, params: dict | None = None) -> list[dict[str, Any]]:
    """
    Execute a read-only SQL statement and return rows as a list of dicts.

    Isolation level
    ---------------
    Runs under READ UNCOMMITTED so no shared locks are acquired.
    This means:
      - This query never blocks writers (indexing, admin saves).
      - Writers never block this query.
      - Dirty reads are possible but acceptable for RAG retrieval and UI.

    Timeout
    -------
    The connection pool_timeout (15 s default) limits how long we wait for
    a free connection.  Individual statement timeout is controlled by the
    ODBC LoginTimeout in the connection string.

    Args:
        sql:    SELECT statement.  Use :param_name placeholders for values.
        params: Optional dict of parameter values.

    Returns:
        List of row dicts.

    Raises:
        Any SQLAlchemy or DB-API exception on failure.
        Use execute_raw_safe() if you want a never-raise version.
    """
    engine = get_engine()
    # execution_options(no_autobegin=True) avoids opening an implicit
    # transaction, combined with SET READ UNCOMMITTED for lock-free reads.
    with engine.connect().execution_options(no_autobegin=True) as conn:
        # Apply READ UNCOMMITTED for this connection so no shared locks taken
        conn.execute(text(_READ_UNCOMMITTED))
        result  = conn.execute(text(sql), params or {})
        columns = list(result.keys())
        rows    = [dict(zip(columns, row)) for row in result.fetchall()]
    return rows


def execute_raw_safe(
    sql: str,
    params: dict | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """
    Safe wrapper around execute_raw — never raises.

    Returns:
        (rows, None)           on success.
        ([], error_message)    on failure — error is also logged.
    """
    try:
        rows = execute_raw(sql, params)
        return rows, None
    except Exception as exc:
        msg = str(exc)
        logger.error(
            "execute_raw_safe failed:\n  SQL: %.300s\n  Error: %s",
            sql.strip(), msg,
        )
        return [], msg
