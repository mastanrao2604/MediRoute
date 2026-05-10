import logging
import os
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from typing import List

logger = logging.getLogger("uvicorn.error")

from ..database import get_db
from .. import crud, schemas, models
from ..dependencies import get_current_user
from ..utils.pdf_generator import generate_resume_pdf, generate_german_pdf

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


# â”€â”€â”€ Shared upload helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _save_pdf(file: UploadFile, user_id: int) -> str:
    """
    Validate, size-check, verify PDF magic bytes, save with a stable
    filename ({user_id}_{timestamp}.pdf), and return the stored path.
    """
    # Validate extension
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext != ".pdf":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are accepted.",
        )

    # Validate MIME type from the Content-Type header sent by the browser.
    # Some Android file pickers set this to application/octet-stream for all files;
    # we still accept that case and rely on magic-byte verification below.
    allowed_content_types = {"application/pdf", "application/octet-stream", "binary/octet-stream"}
    ct = (file.content_type or "").split(";")[0].strip().lower()
    if ct and ct not in allowed_content_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are accepted.",
        )

    try:
        contents = file.file.read()
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

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    from datetime import timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
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
    saved_path = _save_pdf(file, current_user.id)  # absolute path
    # Store the absolute path directly — no leading-slash confusion.
    resume_url = saved_path

    # Delete old file if present (resolve old path correctly too)
    if current_user.resume_url:
        old_path = _resolve_path(current_user.resume_url)
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass

    current_user.resume_url = resume_url
    db.commit()

    return {"resume_url": resume_url}


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
    path = _resolve_path(current_user.resume_url)
    if not os.path.exists(path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume file not found on server. Please re-upload your resume.")
    return FileResponse(path=path, media_type="application/pdf", filename="my_resume.pdf", headers={"Content-Disposition": 'attachment; filename="my_resume.pdf"'})


@router.delete("/me/file", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_resume(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Delete the current user's uploaded resume (file + clears URL)."""
    if not current_user.resume_url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No uploaded resume found.")
    path = _resolve_path(current_user.resume_url)
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

    path = _resolve_path(candidate.resume_url)
    if not os.path.exists(path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume file not found on server.")

    return FileResponse(path=path, media_type="application/pdf", filename=f"resume_{user_id}.pdf")


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
    os.makedirs(PDF_DIR, exist_ok=True)
    file_path = os.path.join(PDF_DIR, f"resume_{current_user.id}.pdf")
    try:
        generate_resume_pdf(data, file_path)
        logger.info("Resume PDF generated: user_id=%s", current_user.id)
    except Exception as exc:
        logger.error("Resume PDF generation failed: user_id=%s error=%s", current_user.id, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PDF generation failed — {exc}",
        )
    return FileResponse(
        path=file_path,
        media_type="application/pdf",
        filename="resume.pdf",
        headers={"Content-Disposition": 'attachment; filename="resume.pdf"'},
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
    os.makedirs(PDF_DIR, exist_ok=True)
    file_path = os.path.join(PDF_DIR, f"german_resume_{current_user.id}.pdf")
    try:
        generate_german_pdf(data, file_path)
        logger.info("German resume PDF generated: user_id=%s", current_user.id)
    except Exception as exc:
        logger.error("German PDF generation failed: user_id=%s error=%s", current_user.id, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PDF generation failed — {exc}",
        )
    return FileResponse(
        path=file_path,
        media_type="application/pdf",
        filename="german_resume.pdf",
        headers={"Content-Disposition": 'attachment; filename="german_resume.pdf"'},
    )


# â”€â”€â”€ Photo upload â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

PHOTO_DIR: str = os.getenv("PHOTO_DIR", PHOTO_DIR_DEFAULT)


@router.post("/photo")
def upload_resume_photo(
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user),
):
    """Upload a profile photo for use in the resume PDF. Returns the saved file path."""
    allowed_types = {"image/jpeg", "image/png", "image/webp"}
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only JPEG, PNG, and WebP images are supported.",
        )
    os.makedirs(PHOTO_DIR, exist_ok=True)
    ext = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
    filename = f"photo_{current_user.id}_{uuid.uuid4().hex[:8]}{ext}"
    dest = os.path.join(PHOTO_DIR, filename)
    try:
        contents = file.file.read()
        with open(dest, "wb") as f:
            f.write(contents)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save photo: {exc}",
        )
    finally:
        file.file.close()
    return {"photo_url": dest}
