"""Supabase Storage helper for MediRoute resume/photo persistence.

WHY THIS EXISTS
---------------
Render free-tier instances have an EPHEMERAL local filesystem — every
deploy or restart wipes uploaded files.  This module routes all file
I/O through Supabase Storage so uploads survive Render restarts.

REQUIRED SETUP (one-time, manual)
----------------------------------
1. In the Supabase dashboard → Storage → New bucket:
     Name: resumes   | Public: OFF (private)
     Name: photos    | Public: OFF (private)
2. In Render → Environment:
     SUPABASE_URL         = https://xxxx.supabase.co
     SUPABASE_SERVICE_KEY = <service_role_key>   (NOT the anon key)

LOCAL DEVELOPMENT
-----------------
Without those env vars the module returns None from _get_client().
Callers catch RuntimeError and fall back to local disk (dev only).
"""

import logging
import os
import tempfile
from typing import Optional

logger = logging.getLogger("uvicorn.error")

# ── Constants ─────────────────────────────────────────────────────────────────
BUCKET_RESUMES = "resumes"
BUCKET_PHOTOS  = "photos"

_SUPABASE_URL = os.getenv("SUPABASE_URL", "")
_SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

_client = None


def _get_client():
    """Lazy-init the Supabase client (module-level singleton)."""
    global _client
    if _client is not None:
        return _client
    if not _SUPABASE_URL or not _SUPABASE_KEY:
        return None
    try:
        from supabase import create_client  # imported lazily so missing package doesn't break startup
        _client = create_client(_SUPABASE_URL, _SUPABASE_KEY)
        logger.info("Supabase Storage client initialised.")
    except Exception as exc:
        logger.error("Failed to initialise Supabase client: %s", exc)
    return _client


# ── Path classification ───────────────────────────────────────────────────────

def is_supabase_path(path: str) -> bool:
    """Return True if the stored DB value is a Supabase Storage object key.

    Supabase keys look like:  'resumes/42_20240101120000.pdf'
    Legacy local paths look like: '/opt/render/project/src/.../file.pdf'
    """
    return (
        bool(path)
        and not path.startswith("/")
        and (path.startswith("resumes/") or path.startswith("photos/"))
    )


# ── Upload ────────────────────────────────────────────────────────────────────

def upload_resume(file_bytes: bytes, user_id: int, timestamp: str) -> str:
    """Upload resume PDF bytes to Supabase Storage.

    Returns the storage object key, e.g. 'resumes/42_20240101120000.pdf'.
    Raises RuntimeError if Supabase is unavailable.
    """
    client = _get_client()
    if client is None:
        raise RuntimeError(
            "Supabase Storage is not configured. "
            "Set SUPABASE_URL and SUPABASE_SERVICE_KEY on Render."
        )
    object_key = f"resumes/{user_id}_{timestamp}.pdf"
    try:
        client.storage.from_(BUCKET_RESUMES).upload(
            path=object_key,
            file=file_bytes,
            file_options={"content-type": "application/pdf", "upsert": "true"},
        )
        logger.info("Uploaded resume to Supabase Storage: %s", object_key)
        return object_key
    except Exception as exc:
        logger.error("Supabase resume upload failed: %s", exc)
        raise RuntimeError(f"Storage upload failed: {exc}") from exc


def upload_photo(file_bytes: bytes, user_id: int, ext: str, uid: str) -> str:
    """Upload photo bytes to Supabase Storage.

    Returns the storage object key, e.g. 'photos/42_abc123.jpg'.
    Raises RuntimeError if Supabase is unavailable.
    """
    client = _get_client()
    if client is None:
        raise RuntimeError("Supabase Storage is not configured.")
    content_type_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png",  ".webp": "image/webp",
    }
    object_key = f"photos/{user_id}_{uid}{ext}"
    try:
        client.storage.from_(BUCKET_PHOTOS).upload(
            path=object_key,
            file=file_bytes,
            file_options={"content-type": content_type_map.get(ext, "image/jpeg"), "upsert": "true"},
        )
        logger.info("Uploaded photo to Supabase Storage: %s", object_key)
        return object_key
    except Exception as exc:
        logger.error("Supabase photo upload failed: %s", exc)
        raise RuntimeError(f"Storage upload failed: {exc}") from exc


# ── Download ──────────────────────────────────────────────────────────────────

def download_bytes(bucket: str, object_key: str) -> bytes:
    """Download a file from Supabase Storage and return its raw bytes.

    Raises RuntimeError on any failure (not found, network error, etc.).
    """
    client = _get_client()
    if client is None:
        raise RuntimeError("Supabase Storage is not configured.")
    try:
        data = client.storage.from_(bucket).download(object_key)
        return data
    except Exception as exc:
        logger.error("Supabase download failed for %s/%s: %s", bucket, object_key, exc)
        raise RuntimeError(f"Could not retrieve file: {exc}") from exc


def download_photo_to_tempfile(object_key: str) -> Optional[str]:
    """Download a photo from Supabase Storage to a local temp file.

    Used by PDF generation (ReportLab needs a local file path).
    Returns the temp file path on success, or None on failure.
    THE CALLER must delete the temp file after use (os.unlink).
    """
    try:
        photo_bytes = download_bytes(BUCKET_PHOTOS, object_key)
        suffix = "." + object_key.rsplit(".", 1)[-1] if "." in object_key else ".jpg"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(photo_bytes)
        tmp.close()
        return tmp.name
    except Exception as exc:
        logger.warning("Failed to download photo for PDF generation (%s): %s", object_key, exc)
        return None


# ── Delete ────────────────────────────────────────────────────────────────────

def delete_object(bucket: str, object_key: str) -> None:
    """Delete a file from Supabase Storage. Best-effort — ignores errors."""
    client = _get_client()
    if client is None:
        return
    try:
        client.storage.from_(bucket).remove([object_key])
        logger.info("Deleted from Supabase Storage: %s/%s", bucket, object_key)
    except Exception as exc:
        logger.warning("Supabase delete failed for %s/%s: %s", bucket, object_key, exc)
