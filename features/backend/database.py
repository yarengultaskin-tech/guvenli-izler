"""SQLAlchemy engine: local SQLite by default (no Docker)."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.url import make_url

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

_engine: Engine | None = None


def get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _sqlite_url_for_path(path: Path) -> str:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path.as_posix()}"


def get_database_url() -> str:
    """
    SQLite by default. A leftover PostgreSQL `DATABASE_URL` in `.env` is ignored
    so local runs do not fail when Docker is off.
    """
    explicit_database_url = os.getenv("DATABASE_URL", "").strip()
    if explicit_database_url.lower().startswith("sqlite"):
        try:
            parsed = make_url(explicit_database_url)
            if parsed.database and parsed.database != ":memory:":
                db_path = Path(parsed.database)
                if not db_path.is_absolute():
                    db_path = get_project_root() / db_path
                return _sqlite_url_for_path(db_path)
        except Exception:
            pass
        return explicit_database_url

    sqlite_env = os.getenv("SQLITE_PATH", "").strip()
    if sqlite_env:
        db_path = Path(sqlite_env)
        if not db_path.is_absolute():
            db_path = get_project_root() / db_path
        return _sqlite_url_for_path(db_path)

    default_file = get_project_root() / "data" / "guvenli_izler.db"
    return _sqlite_url_for_path(default_file)


REQUIRED_TRACE_COLUMNS: frozenset[str] = frozenset(
    ("id", "latitude", "longitude", "tag_type", "created_at", "user_fingerprint")
)


def _existing_user_traces_columns(conn: Connection) -> set[str]:
    row = conn.execute(
        text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_traces'"
        )
    ).fetchone()
    if row is None:
        return set()
    info = conn.execute(text("PRAGMA table_info(user_traces)")).fetchall()
    return {str(col[1]) for col in info}


def _needs_trace_table_rebuild(existing: set[str]) -> bool:
    if not existing:
        return False
    missing_core = not {
        "latitude",
        "longitude",
        "tag_type",
        "created_at",
    }.issubset(existing)
    if missing_core:
        return True
    if existing & {"geom", "geometry"}:
        return True
    if "user_fingerprint" not in existing:
        return True
    return not REQUIRED_TRACE_COLUMNS.issubset(existing)


def ensure_schema(engine: Engine) -> None:
    """Create `user_traces` and indexes; rebuild table if schema is incompatible."""

    statements_create = [
        """
        CREATE TABLE IF NOT EXISTS user_traces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            tag_type TEXT NOT NULL
                CHECK (tag_type IN ('Güvenli', 'Az Işıklı', 'Issız')),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            user_fingerprint TEXT
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_user_traces_bbox
            ON user_traces (latitude, longitude)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_user_traces_created_at
            ON user_traces (created_at DESC)
        """,
    ]
    try:
        with engine.begin() as conn:
            # Reduce "database is locked" hangs on Windows when multiple processes
            # (Streamlit, uvicorn reloaders) touch the DB concurrently.
            try:
                conn.execute(text("PRAGMA busy_timeout = 5000"))
                conn.execute(text("PRAGMA journal_mode = WAL"))
            except Exception:
                pass

            existing = _existing_user_traces_columns(conn)
            if _needs_trace_table_rebuild(existing):
                conn.execute(text("DROP TABLE IF EXISTS user_traces"))
            for stmt in statements_create:
                conn.execute(text(stmt))

            final_cols = _existing_user_traces_columns(conn)
            need = {"latitude", "longitude", "tag_type", "created_at"}
            if not need.issubset(final_cols):
                raise RuntimeError(
                    f"user_traces schema incomplete after migration: {sorted(final_cols)}"
                )
    except Exception:
        raise


def get_engine() -> Engine:
    global _engine
    if _engine is not None:
        return _engine

    url = get_database_url()
    connect_args = {"check_same_thread": False, "timeout": 2} if url.startswith("sqlite") else {}
    engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)
    try:
        ensure_schema(engine)
    except Exception as exc:
        # If the on-disk DB is locked/corrupt, fall back to in-memory DB so the
        # rest of the app (layers + routing) can still run.
        try:
            engine.dispose()
        except Exception:
            pass
        engine = create_engine(
            "sqlite:///:memory:",
            pool_pre_ping=True,
            connect_args={"check_same_thread": False},
        )
        ensure_schema(engine)
    _engine = engine
    return _engine


def reset_engine_for_tests() -> None:
    """Clear cached engine (e.g. tests)."""
    global _engine
    if _engine is not None:
        try:
            _engine.dispose()
        except Exception:
            pass
    _engine = None


if __name__ == "__main__":
    reset_engine_for_tests()
    _eng = get_engine()
    print("database_ready", get_database_url())
