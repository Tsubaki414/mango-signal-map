"""Signal Map database session management.

Deliberately a different file from the BD system's ``kol.db``. The migration
reads that file once; nothing here ever writes to it, so the hand-collected
source data stays an intact archive and a bad migration is always re-runnable.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

# Imported for its side effect: these tables register on the same metadata, so
# ``init_db`` creates them too. Without this, a fresh database would silently
# lack the follow-graph tables and the BD candidate layer.
from . import bd_models, observation_models  # noqa: F401

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = (
    Path(os.environ["SIGNAL_MAP_DB_PATH"])
    if os.environ.get("SIGNAL_MAP_DB_PATH")
    else BASE_DIR / "data" / "signal_map.db"
)

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    """SQLite disables foreign keys per connection by default, so declaring
    ``ForeignKey`` in the models is not enough on its own -- without this a
    write could create an orphan row that only a later audit discovers."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        # 60s, not 10: collection scripts hold write transactions while
        # paginating an API, and a concurrent writer that gives up after 10s
        # loses the whole run. Learned by losing one -- a stage-3 pass died
        # mid-way because an LLM pass was writing at the same time.
        cursor.execute("PRAGMA busy_timeout=60000")
        # WAL lets a reader work while a writer holds the file, which is the
        # common shape here (API serving reads while a script collects).
        cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return SessionLocal()


def session_dependency():
    """FastAPI dependency yielding one session per request.

    Defined here rather than in each router so there is exactly **one**
    override point. Two routers with two dependencies means a test can override
    one and silently leave the other pointed at the real database -- which is
    how an isolation test passes while the endpoint it guards is untested.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
