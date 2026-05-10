import logging
import os
import re
import uuid
from datetime import datetime
from types import SimpleNamespace
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session
from typing import List

logger = logging.getLogger("uvicorn.error")

from ..database import get_db
from .. import crud, schemas, models
from ..dependencies import get_current_user
from ..utils.pdf_generator import generate_resume_pdf, generate_german_pdf
from ..utils import storage
from ..utils.storage import BUCKET_RESUMES, BUCKET_PHOTOS

router = APIRouter(prefix="/resume", tags=["Resume"])

# Use absolute paths so file I/O works regardless of the working directory
# uvicorn is launched from (critical on Render where cwd != project root).
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_DIR: str    = os.getenv("PDF_DIR",    os.path.join(_BACKEND_ROOT, "generated_pdfs"))
UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", os.path.join(_BACKEND_ROOT, "uploads", "resumes"))
PHOTO_DIR_DEFAULT = os.path.join(_BACKEND_ROOT, "uploads", "photos")

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB
_PDF_MAGIC = b"%PDF"


def _resolve_path(stored: str) -> str:
    """Convert a stored resume_url value to an absolute filesystem path.
    Handles both legacy relative paths (e.g. 'uploads/resumes/1_20240101.pdf')
    and absolute paths stored by the updated upload handler.
    """
    if os.path.isabs(stored):
        return stored
    # Legacy: strip leading slash then resolve relative to backend root
    return os.path.join(_BACKEND_ROOT, stored.lstrip("/").lstrip("\\"))


def _safe_firstname(name: str) -> str:
    """Return a safe, lowercase first-name slug for use in download filenames.

    Rules:
    - Take the first word of the name (first name only)
    - Lowercase
    - Keep only ASCII letters, digits, and hyphens (strip everything else)
    - Fall back to 'user' if nothing remains after sanitization

    Examples:
        'Mastan Rao'  -> 'mastan'
        'Priya'       -> 'priya'
        'John Doe'    -> 'john'
        '  '          -> 'user'
        'José'        -> 'jos'  (non-ASCII stripped for filesystem safety)
    """
    first = (name or "").strip().split()[0] if (name or "").strip() else ""
    slug = re.sub(r"[^a-z0-9-]", "", first.lower())
    return slug if slug else "user"


# â”€â”€â”€ Shared upload helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _save_pdf(file: UploadFile, user_id: int) -> str:
    """
    Validate, size-check, verify PDF magic bytes, then store the file.

    Storage priority:
      1. Supabase Storage  (production — survives Render restarts/deploys)
      2. Local disk fallback  (dev only — ephemeral on Render, will be lost)

    Returns a Supabase object key ('resumes/…') or a local absolute path.
    """
    # Validate extension
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext != ".pdf":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are accepted.",
        )

    # Validate MIME type from the Content-Type header sent by the browser.
    # Some Android file pickers send application/octet-stream for all files;
    # we still accept that and rely on magic-byte verification below.
    allowed_content_types = {"application/pdf", "application/octet-stream", "binary/octet-stream"}
    ct = (file.content_type or "").split(";")[0].strip().lower()
    if ct and ct not in allowed_content_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are accepted.",
        )

    # Read with a hard cap — rejects oversized uploads BEFORE buffering the full
    # body, preventing memory abuse / DoS on Render's 512 MB RAM limit.
    try:
        contents = file.file.read(MAX_UPLOAD_BYTES + 1)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read uploaded file: {exc}",
        )
    finally:
        file.file.close()

    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File too large. Maximum allowed size is 5 MB.",
        )

    if not contents.startswith(_PDF_MAGIC):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File does not appear to be a valid PDF.",
        )

    from datetime import timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    # Attempt Supabase Storage first (production-safe, persists across Render restarts)
    try:
        return storage.upload_resume(contents, user_id, ts)
    except RuntimeError:
        # Supabase not configured — fall back to local disk (dev environment only).
        # WARNING: local files are wiped on every Render deploy. Not for production.
        logger.warning(
            "Supabase Storage unavailable — falling back to local disk (user_id=%s). "
            "Set SUPABASE_URL and SUPABASE_SERVICE_KEY on Render for production.",
            user_id,
        )

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    filename = f"{user_id}_{ts}.pdf"  # always .pdf — never from client input
    dest = os.path.join(UPLOAD_DIR, filename)
    try:
        with open(dest, "wb") as f:
            f.write(contents)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save file: {exc}",
        )
    return dest


# â”€â”€â”€ Resume upload â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.post("/upload")
def upload_resume(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Upload a PDF resume.
    - Deletes the previous uploaded resume file if one exists.
    - Saves the new file and stores its URL on the user record.
    """
    object_key = _save_pdf(file, current_user.id)

    # Delete old file from whichever storage it lives in
    if current_user.resume_url:
        old = current_user.resume_url
        if storage.is_supabase_path(old):
            storage.delete_object(BUCKET_RESUMES, old)
        else:
            old_path = _resolve_path(old)
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except OSError:
                    pass

    current_user.resume_url = object_key
    db.commit()

    # Return a safe success message — do NOT expose storage paths in the response.
    return {"message": "Resume uploaded successfully."}


# â”€â”€â”€ Candidate: view / delete own resume â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get("/me")
def get_my_resume_status(
    current_user: models.User = Depends(get_current_user),
):
    """Return whether the current user has an uploaded resume."""
    return {"has_resume": bool(current_user.resume_url), "resume_url": current_user.resume_url}


@router.get("/me/file")
def get_my_resume_file(
    current_user: models.User = Depends(get_current_user),
):
    """Stream the current user's uploaded resume PDF."""
    if not current_user.resume_url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No uploaded resume found.")

    dl_name = f"{_safe_firstname(current_user.name)}_resume.pdf"
    stored = current_user.resume_url

    if storage.is_supabase_path(stored):
        # Production path: serve from Supabase Storage (survives Render restarts)
        try:
            file_bytes = storage.download_bytes(BUCKET_RESUMES, stored)
        except RuntimeError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Resume file not found. Please re-upload your resume.",
            )
        return Response(
            content=file_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{dl_name}"'},
        )

    # Legacy local path — may be gone after Render restart
    path = _resolve_path(stored)
    if not os.path.exists(path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resume file not found on server. Please re-upload your resume.",
        )
    return FileResponse(path=path, media_type="application/pdf", filename=dl_name,
                        headers={"Content-Disposition": f'attachment; filename="{dl_name}"'})


@router.delete("/me/file", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_resume(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Delete the current user's uploaded resume (file + clears URL)."""
    if not current_user.resume_url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No uploaded resume found.")
    stored = current_user.resume_url
    if storage.is_supabase_path(stored):
        storage.delete_object(BUCKET_RESUMES, stored)
    else:
        path = _resolve_path(stored)
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    current_user.resume_url = None
    db.commit()


# â”€â”€â”€ Recruiter: secure download â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get("/download/{user_id}")
def download_candidate_resume(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Secure resume download.
    - Candidates can download their own resume only.
    - Recruiters can only download resumes of candidates who applied to their jobs.
    """
    if current_user.id != user_id:
        if current_user.role == models.UserRole.recruiter:
            has_access = (
                db.query(models.Application)
                .join(models.Job, models.Application.job_id == models.Job.id)
                .filter(
                    models.Application.user_id == user_id,
                    models.Job.posted_by_user_id == current_user.id,
                )
                .first()
            )
            if not has_access:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: this candidate has not applied to your jobs.",
                )
        else:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")

    candidate = db.query(models.User).filter(models.User.id == user_id).first()
    if not candidate:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found.")
    if not candidate.resume_url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This candidate has not uploaded a resume.")

    dl_name = f"{_safe_firstname(candidate.name)}_resume.pdf"
    stored = candidate.resume_url

    if storage.is_supabase_path(stored):
        try:
            file_bytes = storage.download_bytes(BUCKET_RESUMES, stored)
        except RuntimeError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume file not found on server.")
        return Response(
            content=file_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{dl_name}"'},
        )

    path = _resolve_path(stored)
    if not os.path.exists(path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume file not found on server.")
    return FileResponse(path=path, media_type="application/pdf", filename=dl_name,
                        headers={"Content-Disposition": f'attachment; filename="{dl_name}"'})


# â”€â”€â”€ Resume builder (JSON-stored) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.post("/build", response_model=schemas.ResumeResponse, status_code=status.HTTP_201_CREATED)
def build_resume(
    data: schemas.ResumeBuilderCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return crud.create_generated_resume(
        db=db,
        user_id=current_user.id,
        resume_data=data.model_dump(exclude_none=True),
    )


# â”€â”€â”€ Legacy structured resume builder (ResumeData table) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.post("/builder", response_model=schemas.ResumeBuilderResponse, status_code=status.HTTP_201_CREATED)
def create_resume_builder(
    data: schemas.ResumeBuilderCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    result = crud.create_resume_data(db=db, user_id=current_user.id, data=data.model_dump())
    crud.update_profile_from_resume_builder(db=db, user_id=current_user.id, resume_data=data.model_dump())
    return result


@router.get("/builder/me", response_model=List[schemas.ResumeBuilderResponse])
def get_resume_builder(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return crud.get_resume_data(db, current_user.id)


# â”€â”€â”€ PDF generation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get("/builder/pdf")
def download_resume_pdf(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    resumes = crud.get_resume_data(db, current_user.id)
    if not resumes:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No resume data found. Please save your resume first, then download.",
        )
    data = resumes[-1]

    # Resolve photo: if stored as a Supabase key, download to a temp file so
    # ReportLab can read it as a local path during PDF generation.
    temp_photo_path = None
    if data.photo_url:
        if storage.is_supabase_path(data.photo_url):
            temp_photo_path = storage.download_photo_to_tempfile(data.photo_url)
        elif os.path.exists(data.photo_url):
            temp_photo_path = data.photo_url

    # Build a snapshot to pass to the generator — avoids mutating the ORM object.
    pdf_data = SimpleNamespace(
        full_name=data.full_name,
        email=data.email,
        phone=data.phone,
        location=data.location,
        profile_summary=data.profile_summary,
        education=data.education,
        experience=data.experience,
        skills=data.skills,
        languages=data.languages,
        photo_url=temp_photo_path or "",
    )

    os.makedirs(PDF_DIR, exist_ok=True)
    file_path = os.path.join(PDF_DIR, f"resume_{current_user.id}.pdf")
    try:
        generate_resume_pdf(pdf_data, file_path)
        logger.info("Resume PDF generated: user_id=%s", current_user.id)
    except Exception as exc:
        logger.error("Resume PDF generation failed: user_id=%s error=%s", current_user.id, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PDF generation failed — {exc}",
        )
    finally:
        # Remove temp photo file only if we downloaded it from Supabase
        if temp_photo_path and storage.is_supabase_path(data.photo_url or ""):
            try:
                os.unlink(temp_photo_path)
            except OSError:
                pass

    dl_name = f"{_safe_firstname(current_user.name)}_resume.pdf"
    return FileResponse(
        path=file_path,
        media_type="application/pdf",
        filename=dl_name,
        headers={"Content-Disposition": f'attachment; filename="{dl_name}"'},
    )


@router.get("/builder/pdf/german")
def download_german_resume_pdf(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    resumes = crud.get_resume_data(db, current_user.id)
    if not resumes:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No resume data found. Please save your resume first, then download.",
        )
    data = resumes[-1]

    temp_photo_path = None
    if data.photo_url:
        if storage.is_supabase_path(data.photo_url):
            temp_photo_path = storage.download_photo_to_tempfile(data.photo_url)
        elif os.path.exists(data.photo_url):
            temp_photo_path = data.photo_url

    pdf_data = SimpleNamespace(
        full_name=data.full_name,
        email=data.email,
        phone=data.phone,
        location=data.location,
        profile_summary=data.profile_summary,
        education=data.education,
        experience=data.experience,
        skills=data.skills,
        languages=data.languages,
        photo_url=temp_photo_path or "",
    )

    os.makedirs(PDF_DIR, exist_ok=True)
    file_path = os.path.join(PDF_DIR, f"german_resume_{current_user.id}.pdf")
    try:
        generate_german_pdf(pdf_data, file_path)
        logger.info("German resume PDF generated: user_id=%s", current_user.id)
    except Exception as exc:
        logger.error("German PDF generation failed: user_id=%s error=%s", current_user.id, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PDF generation failed — {exc}",
        )
    finally:
        if temp_photo_path and storage.is_supabase_path(data.photo_url or ""):
            try:
                os.unlink(temp_photo_path)
            except OSError:
                pass

    dl_name = f"{_safe_firstname(current_user.name)}_german_resume.pdf"
    return FileResponse(
        path=file_path,
        media_type="application/pdf",
        filename=dl_name,
        headers={"Content-Disposition": f'attachment; filename="{dl_name}"'},
    )


# â”€â”€â”€ Photo upload â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

PHOTO_DIR: str = os.getenv("PHOTO_DIR", PHOTO_DIR_DEFAULT)


@router.post("/photo")
def upload_resume_photo(
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user),
):
    """Upload a profile photo for use in the resume PDF. Returns the storage key."""
    allowed_types = {"image/jpeg", "image/png", "image/webp"}
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only JPEG, PNG, and WebP images are supported.",
        )
    ext = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
    uid = uuid.uuid4().hex[:8]
    # Bounded read — reject oversized images before buffering full body
    try:
        contents = file.file.read(2 * 1024 * 1024 + 1)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read photo: {exc}",
        )
    finally:
        file.file.close()
    if len(contents) > 2 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Image too large. Maximum size is 2 MB.",
        )
    # Attempt Supabase Storage first; fall back to local disk for dev
    try:
        object_key = storage.upload_photo(contents, current_user.id, ext, uid)
        return {"photo_url": object_key}
    except RuntimeError:
        logger.warning(
            "Supabase Storage unavailable — falling back to local disk for photo user_id=%s.",
            current_user.id,
        )
    os.makedirs(PHOTO_DIR, exist_ok=True)
    filename = f"photo_{current_user.id}_{uid}{ext}"
    dest = os.path.join(PHOTO_DIR, filename)
    try:
        with open(dest, "wb") as f:
            f.write(contents)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save photo: {exc}",
        )
    return {"photo_url": dest}
