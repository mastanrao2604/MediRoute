import os
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent / ".env")

from sqlalchemy import create_engine, text

engine = create_engine(os.getenv("DATABASE_URL"), pool_pre_ping=True)

with engine.begin() as conn:
    admin_phone = "9493206268"
    admin = conn.execute(text("SELECT id FROM users WHERE phone = :p"), {"p": admin_phone}).fetchone()
    admin_id = admin[0] if admin else None
    print(f"Admin user ID: {admin_id}")

    for table in ["applications", "resumes", "profiles", "user_preferences", "jobs", "refresh_tokens"]:
        conn.execute(text(f"DELETE FROM {table}"))
        print(f"{table} cleared")

    if admin_id:
        conn.execute(text("DELETE FROM users WHERE id != :id"), {"id": admin_id})
        print(f"users cleared (kept admin id={admin_id})")
    else:
        conn.execute(text("DELETE FROM users"))
        print("all users cleared (no admin found)")

    count = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
    print(f"Users remaining: {count}")

print("Done!")
