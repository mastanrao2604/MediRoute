"""
Aggregate dashboard endpoint.

GET /dashboard
  Returns profile + preferences + recent applications in a single request,
  replacing three separate API calls from the frontend.
  Only for candidate roles (not recruiters).
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from ..database import get_db
from .. import crud, schemas, models
from ..dependencies import require_candidate

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/", response_model=schemas.DashboardResponse)
def get_dashboard(
    app_limit: Optional[int] = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_candidate),
):
    """Return all candidate dashboard data in one request.

    Eliminates 3 separate frontend API calls (profile, preferences, applications)
    and replaces them with a single round-trip that runs 3 parallel-ish DB queries.
    """
    data = crud.get_dashboard_data(db, current_user.id, app_limit=app_limit)
    return schemas.DashboardResponse(
        profile=data["profile"],
        preferences=data["preferences"],
        applications=data["applications"],
    )
