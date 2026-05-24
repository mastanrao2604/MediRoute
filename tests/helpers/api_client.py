"""HTTP client for operational regression tests."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from .config import CITY_ID, HOSP_LAT, HOSP_LNG, HOSP_PIN


class ApiError(Exception):
    def __init__(self, response: httpx.Response):
        self.response = response
        try:
            body = response.json()
        except Exception:
            body = response.text[:500]
        super().__init__(f"HTTP {response.status_code}: {body}")


class MediRouteClient:
    def __init__(self, base_url: str, token: Optional[str] = None, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def _headers(self, extra: Optional[dict] = None) -> dict:
        h = dict(extra or {})
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = self._headers(kwargs.pop("headers", None))
        resp = self._client.request(method, path, headers=headers, **kwargs)
        return resp

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs) -> httpx.Response:
        return self.request("PUT", path, **kwargs)

    def json_or_fail(self, resp: httpx.Response) -> Any:
        if resp.status_code >= 400:
            raise ApiError(resp)
        if not resp.content:
            return {}
        return resp.json()

    def health(self) -> dict:
        return self.json_or_fail(self.get("/health"))

    def me(self) -> dict:
        return self.json_or_fail(self.get("/auth/me"))

    def reconcile(self, trigger: str = "test") -> dict:
        return self.json_or_fail(self.get("/dispatch/reconcile", params={"trigger": trigger}))

    def list_shifts(self) -> dict:
        return self.json_or_fail(self.get("/shifts/"))

    def create_shift(
        self,
        *,
        idempotency_key: Optional[str] = None,
        shift_start: Optional[datetime] = None,
        hospital_name: str = "Regression Test Hospital",
        urgency: str = "emergency",
    ) -> dict:
        start = shift_start or (datetime.now(timezone.utc) + timedelta(hours=2))
        payload = {
            "role_required": "nurse",
            "hospital_name": hospital_name,
            "hospital_latitude": HOSP_LAT,
            "hospital_longitude": HOSP_LNG,
            "hospital_pincode": HOSP_PIN,
            "shift_start": start.isoformat().replace("+00:00", "Z"),
            "urgency": urgency,
            "city_id": CITY_ID,
            "idempotency_key": idempotency_key or str(uuid.uuid4()),
        }
        return self.json_or_fail(self.post("/shifts/", json=payload))

    def cancel_shift(self, shift_id: int, reason: str = "regression test") -> dict:
        return self.json_or_fail(self.post(f"/shifts/{shift_id}/cancel", json={"reason": reason}))

    def confirm_staff(self, shift_id: int, nurse_user_id: int) -> dict:
        return self.json_or_fail(
            self.post(f"/shifts/{shift_id}/confirm-staff", json={"nurse_user_id": nurse_user_id})
        )

    def accept_offer(self, offer_id: int) -> dict:
        return self.json_or_fail(self.post(f"/dispatch/offers/{offer_id}/accept"))

    def check_in(self, shift_id: int, lat: float = HOSP_LAT, lng: float = HOSP_LNG) -> dict:
        return self.json_or_fail(
            self.post(f"/shifts/{shift_id}/checkin", json={"latitude": lat, "longitude": lng})
        )

    def check_out(self, shift_id: int) -> dict:
        return self.json_or_fail(self.post(f"/shifts/{shift_id}/checkout"))

    def mark_no_show(self, shift_id: int, nurse_user_id: int) -> dict:
        return self.json_or_fail(
            self.post(f"/shifts/{shift_id}/mark-no-show", json={"nurse_user_id": nurse_user_id})
        )

    def pending_offers(self) -> dict:
        return self.json_or_fail(self.get("/dispatch/offers/pending"))

    def set_nurse_online(self) -> dict:
        return self.json_or_fail(
            self.put(
                "/availability/toggle",
                json={
                    "is_available": True,
                    "latitude": HOSP_LAT,
                    "longitude": HOSP_LNG,
                    "city_id": CITY_ID,
                },
            )
        )

    def wait_for_offer_on_shift(self, shift_id: int, timeout_sec: float = 45.0) -> dict:
        import time

        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            data = self.pending_offers()
            for offer in data.get("offers") or []:
                if int(offer.get("shift_id", -1)) == int(shift_id):
                    return offer
            time.sleep(0.4)
        raise TimeoutError(f"No offer for shift {shift_id} within {timeout_sec}s")


def assert_json_serializable(obj: Any) -> None:
    json.dumps(obj, default=str)
