"""Run alembic upgrade head using patched migration copy (writable path)."""
import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "mediroute-backend"
VERIFY = ROOT / "alembic_verify"

load_dotenv(BACKEND / ".env")
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))

cfg = Config(str(BACKEND / "alembic.ini"))
cfg.set_main_option("script_location", str(VERIFY).replace("\\", "/"))

print("Upgrading to head via", VERIFY)
command.upgrade(cfg, "head")
print("alembic upgrade head: OK")
