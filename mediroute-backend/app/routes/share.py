"""
share.py — Public sharing routes for MediRoute jobs.

Provides:
  GET /jobs/public/{job_id}           → JSON: public-safe job fields only
  GET /share/job/{job_id}             → HTML: social share landing page
  GET /.well-known/assetlinks.json    → Android App Links verification

Security:
  - Only status=open jobs are exposed
  - No recruiter contact info, no internal IDs beyond job_id
  - No auth required — these are intentionally public endpoints
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models

logger = logging.getLogger("uvicorn.error")

router = APIRouter(tags=["Share"])

# ── Constants ─────────────────────────────────────────────────────────────────

_BACKEND_URL = "https://mediroute-8az0.onrender.com"
_PLAY_STORE_URL = "https://play.google.com/store/apps/details?id=com.mediroute.app"
# SHA-256 fingerprint of the release keystore (mediroute-release.jks / alias: mediroute)
_SHA256_FINGERPRINT = (
    "7A:11:46:CB:CD:21:F1:6B:63:5E:1E:7C:93:41:52:7F:"
    "5A:33:EF:73:7B:30:4A:94:AC:73:AF:C3:D7:28:50:39"
)


# ── Public schema (safe fields only) ─────────────────────────────────────────

class PublicJobResponse(BaseModel):
    """Public-safe job representation — no recruiter contacts, no internal IDs."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    hospital_name: Optional[str] = None
    location: Optional[str] = None
    job_type: Optional[str] = None
    role_required: Optional[str] = None
    country: Optional[str] = None
    salary: Optional[str] = None
    description: Optional[str] = None
    recruiter_verified: bool = False


def _get_open_job_or_404(job_id: int, db: Session) -> models.Job:
    """Return a job only if it is status=open. Raise 404 otherwise."""
    job = db.query(models.Job).filter(
        models.Job.id == job_id,
        models.Job.status == models.JobStatus.open,
    ).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found or no longer available.",
        )
    return job


def _recruiter_verified(job: models.Job, db: Session) -> bool:
    """Check if the recruiter who posted this job is verified."""
    if not job.posted_by_user_id:
        return False
    user = db.query(models.User).filter(
        models.User.id == job.posted_by_user_id
    ).first()
    return bool(user and user.is_verified)


def _to_public(job: models.Job, db: Session) -> PublicJobResponse:
    return PublicJobResponse(
        id=job.id,
        title=job.title,
        hospital_name=job.hospital_name,
        location=job.location,
        job_type=job.job_type.value if job.job_type else None,
        role_required=job.role_required.value if job.role_required else None,
        country=job.country,
        salary=job.salary,
        description=job.description,
        recruiter_verified=_recruiter_verified(job, db),
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "/jobs/public/{job_id}",
    response_model=PublicJobResponse,
    summary="Public job details (safe fields only)",
)
def get_public_job(job_id: int, db: Session = Depends(get_db)):
    """
    Returns public-safe job data for open jobs only.
    Used by the app's share utility and any third-party integrations.
    Drafts, pending, and closed jobs return 404.
    """
    job = _get_open_job_or_404(job_id, db)
    return _to_public(job, db)


@router.get(
    "/share/job/{job_id}",
    response_class=HTMLResponse,
    summary="Social share landing page",
)
def share_job_page(job_id: int, db: Session = Depends(get_db)):
    """
    HTML share landing page served when a share link is opened in a browser.
    Includes Open Graph meta tags for rich WhatsApp / LinkedIn previews.
    When Android App Links are configured, installed users bypass this page
    and land directly in the app.
    """
    job = _get_open_job_or_404(job_id, db)
    data = _to_public(job, db)
    return HTMLResponse(content=_render_share_page(data), status_code=200)


@router.get(
    "/.well-known/assetlinks.json",
    include_in_schema=False,
)
def assetlinks():
    """
    Android App Links verification file.
    Android verifies this URL at install time to confirm that this server
    authorises the app (com.mediroute.app) to handle its HTTP URLs.
    Must be served at exactly this path with Content-Type: application/json.
    """
    return JSONResponse(
        content=[
            {
                "relation": ["delegate_permission/common.handle_all_urls"],
                "target": {
                    "namespace": "android_app",
                    "package_name": "com.mediroute.app",
                    "sha256_cert_fingerprints": [_SHA256_FINGERPRINT],
                },
            }
        ],
        headers={"Cache-Control": "public, max-age=86400"},
    )


# ── HTML template ─────────────────────────────────────────────────────────────

def _render_share_page(job: PublicJobResponse) -> str:
    """Generate a mobile-first HTML share page with OG meta tags."""

    role_label = (job.role_required or "").replace("_", " ").title()
    location_parts = [p for p in [job.location, job.country] if p]
    location_str = ", ".join(location_parts)

    # OG description — compact, fits WhatsApp/LinkedIn previews
    og_desc_parts = [
        f"📍 {location_str}" if location_str else "",
        f"💰 {job.salary}" if job.salary else "",
        f"👤 {role_label}" if role_label else "",
    ]
    og_description = "  ·  ".join(p for p in og_desc_parts if p) or "Healthcare job on MediRoute"
    og_title = f"{job.title}" + (f" — {job.hospital_name}" if job.hospital_name else "")

    share_url = f"{_BACKEND_URL}/share/job/{job.id}"
    verified_badge = (
        '<span style="display:inline-flex;align-items:center;gap:5px;'
        'background:#dcfce7;color:#166534;border-radius:20px;'
        'padding:4px 12px;font-size:13px;font-weight:600;">'
        '✅ Verified Recruiter</span>' if job.recruiter_verified else ""
    )

    # Build detail rows for non-null fields
    def row(icon: str, label: str, value: str) -> str:
        return (
            f'<div style="display:flex;align-items:center;gap:10px;padding:10px 0;'
            f'border-bottom:1px solid #f1f5f9;">'
            f'<span style="font-size:20px;width:28px;text-align:center;">{icon}</span>'
            f'<div><div style="font-size:11px;color:#94a3b8;text-transform:uppercase;'
            f'letter-spacing:.5px;">{label}</div>'
            f'<div style="font-size:15px;font-weight:600;color:#1e293b;">{value}</div></div>'
            f'</div>'
        )

    details_html = ""
    if location_str:
        details_html += row("📍", "Location", location_str)
    if job.salary:
        details_html += row("💰", "Salary", job.salary)
    if role_label:
        details_html += row("👤", "Role", role_label)
    if job.hospital_name:
        details_html += row("🏥", "Hospital", job.hospital_name)

    desc_html = ""
    if job.description:
        # Truncate long descriptions for the share page
        desc = job.description[:400] + "…" if len(job.description) > 400 else job.description
        desc_html = (
            f'<div style="margin-top:12px;padding:14px;background:#f8fafc;'
            f'border-radius:10px;">'
            f'<div style="font-size:12px;color:#64748b;font-weight:600;'
            f'margin-bottom:6px;">JOB DESCRIPTION</div>'
            f'<div style="font-size:14px;color:#475569;line-height:1.6;'
            f'white-space:pre-line;">{desc}</div>'
            f'</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{og_title} | MediRoute</title>

  <!-- Open Graph — WhatsApp / LinkedIn / Telegram previews -->
  <meta property="og:type"        content="website" />
  <meta property="og:url"         content="{share_url}" />
  <meta property="og:title"       content="{og_title}" />
  <meta property="og:description" content="{og_description}" />
  <meta property="og:site_name"   content="MediRoute — Healthcare Jobs" />
  <meta property="og:image"       content="{_BACKEND_URL}/static/share-card.png" />
  <meta property="og:image:width"  content="1200" />
  <meta property="og:image:height" content="630" />

  <!-- Twitter Card -->
  <meta name="twitter:card"        content="summary_large_image" />
  <meta name="twitter:title"       content="{og_title}" />
  <meta name="twitter:description" content="{og_description}" />

  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #f1f5f9;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 20px 16px 40px;
    }}
    .card {{
      background: #fff;
      border-radius: 20px;
      box-shadow: 0 4px 24px rgba(0,0,0,.08);
      max-width: 480px;
      width: 100%;
      overflow: hidden;
    }}
    .header {{
      background: linear-gradient(135deg, #2563eb 0%, #4f46e5 100%);
      padding: 20px 20px 16px;
      color: #fff;
    }}
    .brand {{
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 1px;
      opacity: .8;
      text-transform: uppercase;
      margin-bottom: 10px;
    }}
    .job-title {{
      font-size: 22px;
      font-weight: 800;
      line-height: 1.25;
      margin-bottom: 4px;
    }}
    .hospital {{
      font-size: 15px;
      opacity: .85;
    }}
    .body {{ padding: 18px; }}
    .badge-row {{ margin-bottom: 14px; }}
    .cta-block {{
      margin-top: 18px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}
    .btn {{
      display: block;
      width: 100%;
      padding: 16px;
      border-radius: 14px;
      font-size: 16px;
      font-weight: 700;
      text-align: center;
      text-decoration: none;
      cursor: pointer;
      border: none;
    }}
    .btn-primary {{
      background: #2563eb;
      color: #fff;
    }}
    .btn-outline {{
      background: #fff;
      color: #2563eb;
      border: 2px solid #2563eb;
    }}
    .footer {{
      margin-top: 20px;
      font-size: 12px;
      color: #94a3b8;
      text-align: center;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <div class="brand">MediRoute · Healthcare Jobs</div>
      <div class="job-title">{job.title}</div>
      {f'<div class="hospital">{job.hospital_name}</div>' if job.hospital_name else ''}
    </div>

    <div class="body">
      {f'<div class="badge-row">{verified_badge}</div>' if job.recruiter_verified else ''}

      <div>{details_html}</div>

      {desc_html}

      <div class="cta-block">
        <a href="{_PLAY_STORE_URL}" class="btn btn-primary">
          📲 Download MediRoute &amp; Apply
        </a>
        <a href="{share_url}" class="btn btn-outline">
          🔗 Share this Job
        </a>
      </div>
    </div>
  </div>

  <div class="footer">
    MediRoute — Connecting healthcare professionals with opportunities
  </div>
</body>
</html>"""
