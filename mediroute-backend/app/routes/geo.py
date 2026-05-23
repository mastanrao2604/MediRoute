"""Server-side Nominatim proxy — avoids WebView/CapacitorHttp issues on Android."""
from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
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
# Nominatim usage policy: max 1 request/second per app.
_MIN_NOMINATIM_INTERVAL_SEC = 1.1
_CACHE_TTL_SEC = 86_400
_CACHE_MAX_ENTRIES = 800

_nominatim_lock = threading.Lock()
_last_nominatim_at = 0.0
_geocode_cache: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()


_SKIP_LOCALITY_RE = re.compile(
    r"^(india|telangana|andhra pradesh|hyderabad|hyd|greater hyderabad|telangana zone|\d{6})$",
    re.I,
)

_MICRO_KEYS = (
    "suburb",
    "neighbourhood",
    "quarter",
    "residential",
    "locality",
    "city_district",
    "borough",
    "hamlet",
    "village",
)
_CITY_KEYS = ("town", "city", "municipality")


def _clean_part(raw: Any) -> str:
    v = str(raw or "").strip()
    if not v or _SKIP_LOCALITY_RE.match(v) or re.search(r"\bzone$", v, re.I):
        return ""
    return v


def _first_field(address: dict[str, Any], keys: tuple[str, ...]) -> tuple[str, str] | None:
    for key in keys:
        value = _clean_part(address.get(key))
        if value:
            return key, value
    return None


def _locality_from_display_name(display_name: str) -> tuple[str, str, str] | None:
    parts = [
        p.strip()
        for p in (display_name or "").split(",")
        if p.strip() and not _SKIP_LOCALITY_RE.match(p.strip()) and not re.fullmatch(r"\d{6}", p.strip())
    ]
    for idx, part in enumerate(parts):
        if part.lower() == "hyderabad" and idx > 0:
            candidate = parts[idx - 1]
            if candidate and not re.search(r"\b(road|street|lane|marg|highway|flyover)\b", candidate, re.I):
                return "display_name", candidate, "Hyderabad"
    for part in parts:
        if re.search(r"\b(road|street|lane|marg|highway)\b", part, re.I):
            continue
        if 3 <= len(part) <= 40:
            return "display_name", part, ""
    return None


def _parse_locality(address: dict[str, Any], display_name: str = "") -> dict[str, Any]:
    micro = _first_field(address, _MICRO_KEYS)
    city = _first_field(address, _CITY_KEYS)
    if micro and city and micro[1].lower() == city[1].lower():
        city = None

    if micro:
        city_name = city[1] if city else ""
        if city_name and micro[1].lower() != "hyderabad":
            label = f"{micro[1]}, {city_name}"
        else:
            label = micro[1]
        return {
            "label": label,
            "micro_field": micro[0],
            "city_field": city[0] if city else None,
        }

    from_display = _locality_from_display_name(display_name)
    if from_display:
        field, value, city_hint = from_display
        label = f"{value}, {city_hint}" if city_hint else value
        return {
            "label": label,
            "micro_field": field,
            "city_field": "city" if city_hint else None,
        }

    if city:
        return {"label": city[1], "micro_field": None, "city_field": city[0]}

    return {"label": "", "micro_field": None, "city_field": None}


def _normalize_pincode(raw: str | None) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    return digits[:6] if len(digits) >= 6 else None


def _cache_key(path: str, params: dict[str, Any]) -> tuple:
    lat = params.get("lat")
    lon = params.get("lon")
    if lat is not None and lon is not None:
        return (
            path,
            round(float(lat), 4),
            round(float(lon), 4),
            params.get("zoom"),
        )
    postal = params.get("postalcode")
    if postal:
        return (path, str(postal))
    return (path, tuple(sorted((k, str(v)) for k, v in params.items())))


def _cache_get(key: tuple) -> Any | None:
    row = _geocode_cache.get(key)
    if not row:
        return None
    ts, data = row
    if time.time() - ts > _CACHE_TTL_SEC:
        return None
    return data


def _cache_get_stale(key: tuple) -> Any | None:
    row = _geocode_cache.get(key)
    return row[1] if row else None


def _cache_set(key: tuple, data: Any) -> None:
    _geocode_cache[key] = (time.time(), data)
    _geocode_cache.move_to_end(key)
    while len(_geocode_cache) > _CACHE_MAX_ENTRIES:
        _geocode_cache.popitem(last=False)


def _nominatim_http(path: str, params: dict[str, Any]) -> Any:
    global _last_nominatim_at
    with _nominatim_lock:
        wait = _MIN_NOMINATIM_INTERVAL_SEC - (time.monotonic() - _last_nominatim_at)
        if wait > 0:
            time.sleep(wait)
        last_err: HTTPException | None = None
        for attempt in range(3):
            try:
                res = requests.get(
                    f"{NOMINATIM_BASE}{path}",
                    params=params,
                    headers=NOMINATIM_HEADERS,
                    timeout=TIMEOUT_SEC,
                )
            except requests.RequestException as exc:
                raise HTTPException(
                    status_code=502, detail="Geocode service unreachable"
                ) from exc
            if res.status_code == 429:
                last_err = HTTPException(
                    status_code=503, detail="Geocode rate limited — retry in a moment"
                )
                time.sleep(1.5 * (attempt + 1))
                continue
            if not res.ok:
                raise HTTPException(status_code=502, detail="Geocode service error")
            _last_nominatim_at = time.monotonic()
            return res.json()
        if last_err:
            raise last_err
        raise HTTPException(status_code=502, detail="Geocode service error")


def _nominatim_get(path: str, params: dict[str, Any]) -> Any:
    key = _cache_key(path, params)
    hit = _cache_get(key)
    if hit is not None:
        return hit
    try:
        data = _nominatim_http(path, params)
    except HTTPException as exc:
        if exc.status_code == 503:
            stale = _cache_get_stale(key)
            if stale is not None:
                return stale
        raise
    _cache_set(key, data)
    return data


@router.get("/reverse")
def reverse_geocode(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    _user: models.User = Depends(get_current_user),
):
    data = _nominatim_get(
        "/reverse",
        {"lat": lat, "lon": lng, "format": "json", "addressdetails": 1, "zoom": 18},
    )
    address = data.get("address") or {}
    pincode = _normalize_pincode(address.get("postcode"))
    parsed = _parse_locality(address, data.get("display_name") or "")
    locality = parsed["label"]
    return {
        "pincode": pincode,
        "locality": locality or None,
        "display_name": data.get("display_name"),
        "micro_field": parsed.get("micro_field"),
        "city_field": parsed.get("city_field"),
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
    parsed = _parse_locality(address, place.get("display_name") or "")
    display_name = parsed["label"]
    return {
        "pincode": clean,
        "locality": display_name or None,
        "display_name": place.get("display_name"),
        "lat": float(place["lat"]),
        "lng": float(place["lon"]),
    }
