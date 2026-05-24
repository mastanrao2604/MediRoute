"""Suite 10 — WebSocket stability."""
from __future__ import annotations

import pytest

from tests.helpers.ws_client import (
    run_async,
    ws_connect_once,
    ws_duplicate_connection,
    ws_reconnect_cycle,
)


pytestmark = [pytest.mark.realtime, pytest.mark.critical]


def test_ws_connect_ping_pong(base_url, nurse_token, manifest):
    run_async(ws_connect_once(base_url, manifest["nurse_id"], nurse_token))


def test_reconnect_storm(base_url, nurse_token, manifest):
    run_async(ws_reconnect_cycle(base_url, manifest["nurse_id"], nurse_token, cycles=10))


def test_duplicate_connection_replaces(base_url, nurse_token, manifest):
    run_async(ws_duplicate_connection(base_url, manifest["nurse_id"], nurse_token))


def test_recruiter_ws_stable(base_url, recruiter_token, manifest):
    run_async(ws_reconnect_cycle(base_url, manifest["recruiter_id"], recruiter_token, cycles=5))
