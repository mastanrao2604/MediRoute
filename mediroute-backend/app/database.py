import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Absolute path so SQLite works regardless of the working directory uvicorn is launched from.
_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DB = f"sqlite:///{os.path.abspath(os.path.join(_HERE, '..', 'mediroute.db'))}"

# Reads DATABASE_URL from environment; falls back to local SQLite.
# Swap to PostgreSQL by setting DATABASE_URL=postgresql://user:pass@host/db
DATABASE_URL: str = os.getenv("DATABASE_URL", _DEFAULT_DB)

_is_sqlite = DATABASE_URL.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}

# pool_pre_ping: verify connections before use (essential for PostgreSQL to
# survive DB restarts / idle connection timeouts).
# pool_size / max_overflow: ignored by SQLite (uses StaticPool), meaningful for PG.
if _is_sqlite:
    engine = create_engine(DATABASE_URL, connect_args=_connect_args)
else:
    engine = create_engine(
        DATABASE_URL,
        connect_args={"connect_timeout": 10},
        pool_pre_ping=True,
        pool_size=20,
        max_overflow=40,
        pool_recycle=1800,
        pool_timeout=30,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency — yields a DB session and guarantees close.

    pool_pre_ping=True on the engine already validates connections before
    handing them out (transparent reconnect on stale connections). An extra
    explicit SELECT 1 here would add a network round-trip (~100 ms on Supabase)
    to EVERY request — removed intentionally.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()