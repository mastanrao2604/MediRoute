"""Server-side Nominatim proxy — avoids WebView/CapacitorHttp issues on Android."""
from __future__ import annotations

import re
from typing import Any

import requests
from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import get_current_user
from .. import models

router = APIRouter(prefix="/geo", tags=["Geo"])

NOMINATIM_BASE = "https://nominatim.openstreetmap.org"
NOMINATIM_HEADERS = {
    "Accept-Language": "en",
    "User-Agent": "MediRoute/1.0 (healthcare staffing; support@mediroute.in)",
}
TIMEOUT_SEC = 14


def _format_locality(address: dict[str, Any], display_name: str = "") -> str:
    suburb = (
        address.get("suburb")
        or address.get("neighbourhood")
        or address.get("quarter")
        or address.get("residential")
    )
    town = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("municipality")
    )
    if suburb and town and suburb != town:
        return f"{suburb}, {town}"
    if suburb:
        return suburb
    if town:
        return town
    parts = [p.strip() for p in (display_name or "").split(",") if p.strip()]
    if len(parts) >= 2:
        return ", ".join(parts[:2])
    return parts[0] if parts else ""


def _normalize_pincode(raw: str | None) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    return digits[:6] if len(digits) >= 6 else None


def _nominatim_get(path: str, params: dict[str, Any]) -> Any:
    try:
        res = requests.get(
            f"{NOMINATIM_BASE}{path}",
            params=params,
            headers=NOMINATIM_HEADERS,
            timeout=TIMEOUT_SEC,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail="Geocode service unreachable") from exc
    if res.status_code == 429:
        raise HTTPException(status_code=503, detail="Geocode rate limited — retry in a moment")
    if not res.ok:
        raise HTTPException(status_code=502, detail="Geocode service error")
    return res.json()


@router.get("/reverse")
def reverse_geocode(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    _user: models.User = Depends(get_current_user),
):
    data = _nominatim_get(
        "/reverse",
        {"lat": lat, "lon": lng, "format": "json", "addressdetails": 1},
    )
    address = data.get("address") or {}
    pincode = _normalize_pincode(address.get("postcode"))
    locality = _format_locality(address, data.get("display_name") or "")
    return {
        "pincode": pincode,
        "locality": locality or None,
        "display_name": data.get("display_name"),
        "lat": float(data.get("lat") or lat),
        "lng": float(data.get("lon") or lng),
    }


@router.get("/pincode/{pincode}")
def geocode_pincode(
    pincode: str,
    _user: models.User = Depends(get_current_user),
):
    clean = re.sub(r"\D", "", pincode)
    if len(clean) != 6:
        raise HTTPException(status_code=400, detail="Pincode must be 6 digits")
    rows = _nominatim_get(
        "/search",
        {
            "postalcode": clean,
            "country": "IN",
            "format": "json",
            "limit": 1,
            "addressdetails": 1,
        },
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Pincode not found")
    place = rows[0]
    address = place.get("address") or {}
    display_name = _format_locality(address, place.get("display_name") or "")
    return {
        "pincode": clean,
        "locality": display_name or None,
        "display_name": place.get("display_name"),
        "lat": float(place["lat"]),
        "lng": float(place["lon"]),
    }
