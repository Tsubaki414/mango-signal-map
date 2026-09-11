from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

BASE_DIR = Path(__file__).resolve().parent.parent
# DB_PATH env override lets a deployment point at a mounted persistent volume
# (e.g. Railway) instead of the repo-relative path used for local dev --
# without it, a redeploy's fresh container would silently lose every write.
DB_PATH = Path(os.environ["DB_PATH"]) if os.environ.get("DB_PATH") else BASE_DIR / "data" / "kol.db"

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    """Enforce relational integrity on every application connection.

    SQLite disables foreign keys per connection by default.  Declaring
    ``ForeignKey`` in SQLAlchemy models is not sufficient without this
    pragma, so writes could otherwise create rows that the release audit
    only discovers later.  A bounded busy timeout also makes short concurrent
    dashboard writes wait instead of failing immediately.
    """

    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=10000")
    finally:
        cursor.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return SessionLocal()
