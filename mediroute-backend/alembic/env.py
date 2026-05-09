import os
import sys
from pathlib import Path
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# ─── Make app package importable from mediroute-backend/ ─────────────────────
_here = Path(__file__).resolve().parent.parent   # mediroute-backend/
sys.path.insert(0, str(_here))

# Load .env so DATABASE_URL is available
from dotenv import load_dotenv
load_dotenv(_here / ".env")

# ─── Import app models so autogenerate can detect schema ─────────────────────
from app.database import Base          # noqa: E402
import app.models                      # noqa: E402, F401  (registers all tables)

# ─── Alembic Config ───────────────────────────────────────────────────────────
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url from environment (never use the placeholder in alembic.ini)
# Escape '%' → '%%' because configparser uses % for interpolation
_db_url = os.environ.get("DATABASE_URL")
if _db_url:
    config.set_main_option("sqlalchemy.url", _db_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
