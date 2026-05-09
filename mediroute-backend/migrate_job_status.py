import sqlite3
import os

db_path = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "mediroute.db")
)
con = sqlite3.connect(db_path)
cols = [row[1] for row in con.execute("PRAGMA table_info(jobs)").fetchall()]
if "status" not in cols:
    con.execute("ALTER TABLE jobs ADD COLUMN status VARCHAR NOT NULL DEFAULT 'open'")
    con.commit()
    print("Added status column to jobs table.")
else:
    print("status column already exists — nothing to do.")
con.close()
