import logging
import sqlite3
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, TypeVar

from flask import g, current_app

logger = logging.getLogger("tvw.sqlite")

DB_PATH = "tvw_calculator.db"

T = TypeVar("T")

SQLITE_BUSY_USER_MESSAGE = (
    "The recipe could not be saved because the database was busy. Please try again."
)

SQLITE_BUSY_BENCH_MESSAGE = (
    "The bench is saving this step. Try again in a moment. Your mix has not advanced yet."
)

SQLITE_BUSY_MIN_TIMEOUT_MS = 5000


@dataclass
class SqliteRetryStats:
    attempts: int = 0
    busy_retries: int = 0
    busy_occurrences: list[str] = field(default_factory=list)


def is_sqlite_busy_error(exc: BaseException) -> bool:
    """True for SQLITE_BUSY / database is locked (and similar busy contention)."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def apply_sqlite_connection_pragmas(conn: sqlite3.Connection) -> None:
    """Local-app SQLite settings applied on every connection."""
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=60000;")


def read_sqlite_connection_settings(conn: sqlite3.Connection) -> dict[str, str | int]:
    journal = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
    busy_timeout = int(conn.execute("PRAGMA busy_timeout").fetchone()[0] or 0)
    synchronous = str(conn.execute("PRAGMA synchronous").fetchone()[0])
    return {
        "journal_mode": journal,
        "busy_timeout_ms": busy_timeout,
        "synchronous": synchronous,
    }


def _path_in_onedrive(path: str) -> bool:
    normalized = os.path.normcase(os.path.abspath(path))
    return "onedrive" in normalized


def _flask_reloader_state() -> dict[str, bool | str | None]:
    try:
        debug = bool(current_app.debug)
    except RuntimeError:
        debug = False
    werkzeug_main = os.environ.get("WERKZEUG_RUN_MAIN")
    return {
        "flask_debug": debug,
        "reloader_active": debug and werkzeug_main is not None,
        "reloader_child": debug and werkzeug_main == "true",
        "werkzeug_run_main": werkzeug_main,
    }


def log_sqlite_lock_failure(
    *,
    stage: str,
    exc: sqlite3.OperationalError,
    request_id: str | None = None,
    attempt: int | None = None,
    context: str = "guided_mix",
) -> None:
    """Temporary dev diagnostics for SQLITE_BUSY / database is locked."""
    db_path = ""
    journal_mode = ""
    conn_id = ""
    in_transaction = ""
    try:
        db_path = os.path.abspath(current_app.config.get("DATABASE", DB_PATH))
    except RuntimeError:
        pass
    try:
        conn = g.get("db")
        if conn is not None:
            conn_id = hex(id(conn))
            journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
            in_transaction = str(conn.in_transaction)
    except Exception:
        pass

    reloader = _flask_reloader_state()
    payload = {
        "context": context,
        "stage": stage,
        "error": str(exc),
        "sqlite_errorcode": getattr(exc, "sqlite_errorcode", None),
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "request_id": request_id,
        "attempt": attempt,
        "db_path": db_path,
        "journal_mode": journal_mode,
        "connection_id": conn_id,
        "in_transaction": in_transaction,
        "timestamp_unix": time.time(),
        "in_onedrive": _path_in_onedrive(db_path) if db_path else None,
        **reloader,
    }
    print(f"[sqlite-lock] {payload}")
    logger.warning("sqlite_lock_failure %s", payload)


def log_sqlite_startup_diagnostics(app, *, instance_lock: dict | None = None) -> None:
    """Dev startup log: PID, DB path, reloader, journal mode, busy timeout."""
    pid = os.getpid()
    ppid = os.getppid()
    db_path = os.path.abspath(app.config.get("DATABASE", DB_PATH))
    db_info = app.config.get("DATABASE_INFO") or {}
    debug = bool(app.debug)
    werkzeug_main = os.environ.get("WERKZEUG_RUN_MAIN")
    reloader_active = debug and werkzeug_main is not None
    reloader_child = debug and werkzeug_main == "true"
    use_reloader = os.environ.get("FLASK_USE_RELOADER", "0").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }

    settings: dict[str, str | int] = {}
    try:
        conn = sqlite3.connect(db_path, timeout=60.0)
        apply_sqlite_connection_pragmas(conn)
        settings = read_sqlite_connection_settings(conn)
        conn.close()
    except Exception as exc:
        print(f"[sqlite] pid={pid} ppid={ppid} db={db_path} settings_read_failed={exc}")
        return

    busy_ms = int(settings.get("busy_timeout_ms") or 0)
    print(
        "[sqlite] "
        f"pid={pid} "
        f"ppid={ppid} "
        f"db={db_path} "
        f"flask_debug={debug} "
        f"flask_use_reloader={use_reloader} "
        f"reloader_active={reloader_active} "
        f"reloader_child={reloader_child} "
        f"journal_mode={settings.get('journal_mode')} "
        f"busy_timeout_ms={busy_ms} "
        f"synchronous={settings.get('synchronous')} "
        f"db_source={db_info.get('source', 'unknown')} "
        f"db_migrated={bool(db_info.get('migrated'))} "
        f"in_onedrive={_path_in_onedrive(db_path)}"
    )
    if db_info.get("migrated"):
        print(
            "[sqlite] Migrated database from legacy path: "
            f"{db_info.get('migration_from')}"
        )
    if instance_lock and instance_lock.get("conflict_pid"):
        print(
            "[sqlite] WARNING: Another TVW process may be running "
            f"(pid={instance_lock['conflict_pid']}, lock={instance_lock.get('lock_path')}). "
            "Stop extra app.py instances before mixing."
        )
    if busy_ms < SQLITE_BUSY_MIN_TIMEOUT_MS:
        print(
            f"[sqlite] WARNING: busy_timeout is {busy_ms} ms; "
            f"expected at least {SQLITE_BUSY_MIN_TIMEOUT_MS} ms for bench use."
        )
    if _path_in_onedrive(db_path):
        print(
            "[sqlite] WARNING: SQLite database is inside OneDrive. "
            "Cloud sync can cause lock delays. Set TVW_LOCAL_DATA_DIR or DATABASE_PATH."
        )


def sqlite_retry(
    fn: Callable[[], T],
    *,
    attempts: int = 12,
    base_delay_s: float = 0.05,
    stats: SqliteRetryStats | None = None,
    lock_context: str | None = None,
    lock_stage: str | None = None,
    request_id: str | None = None,
) -> T:
    """Retry on SQLITE_BUSY / database is locked (common with scrapers + UI + cloud-synced folders)."""
    last: sqlite3.OperationalError | None = None
    max_attempts = max(1, attempts)
    for i in range(max_attempts):
        try:
            if stats is not None:
                stats.attempts = i + 1
            return fn()
        except sqlite3.OperationalError as e:
            last = e
            if not is_sqlite_busy_error(e):
                raise
            if stats is not None:
                stats.busy_retries += 1
                stats.busy_occurrences.append(str(e))
            if i + 1 >= max_attempts:
                if lock_stage:
                    log_sqlite_lock_failure(
                        stage=lock_stage,
                        exc=e,
                        request_id=request_id,
                        attempt=i + 1,
                        context=lock_context or "sqlite_retry",
                    )
                break
            time.sleep(base_delay_s * (2 ** min(i, 8)))
    assert last is not None
    raise last


def get_db():
    if "db" not in g:
        conn = sqlite3.connect(
            current_app.config.get("DATABASE", DB_PATH),
            timeout=60.0,
        )
        conn.row_factory = sqlite3.Row
        apply_sqlite_connection_pragmas(conn)
        g.db = conn
    return g.db


def close_db(e=None):
    """Closes the connection at the end of every request."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Initializes the database using the schema.sql provided."""
    db = get_db()

    schema_path = Path(__file__).resolve().parent / "schema.sql"

    if schema_path.exists():
        with schema_path.open(mode="r", encoding="utf-8") as f:
            db.cursor().executescript(f.read())
        db.commit()
    else:
        print("Warning: schema.sql not found. Skipping initialization.")


def init_app(app):
    """Registers the close_db function with the Flask application instance."""
    app.teardown_appcontext(close_db)
