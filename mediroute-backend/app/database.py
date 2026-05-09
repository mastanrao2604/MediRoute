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
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        pool_recycle=1800,  # recycle connections every 30 min
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency — yields a DB session and guarantees close."""
    from fastapi import HTTPException
    from sqlalchemy import text
    db = SessionLocal()
    try:
        # Verify the connection is alive (pool_pre_ping may not catch all cases at yield time).
        # This gives callers a clean 503 instead of an opaque 500.
        try:
            db.execute(text("SELECT 1"))
        except Exception as _ping_err:
            db.close()
            raise HTTPException(
                status_code=503,
                detail="Database temporarily unavailable. Please try again in a moment.",
            )
        yield db
    finally:
        db.close()