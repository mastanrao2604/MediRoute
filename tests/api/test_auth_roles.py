"""Suite 1 — Auth + role integrity."""
from __future__ import annotations

import pytest

from tests.helpers.api_client import assert_json_serializable
from tests.helpers.ws_client import run_async, ws_auth_rejected, ws_connect_once


pytestmark = [pytest.mark.api, pytest.mark.critical]


def test_recruiter_me_role(recruiter_client, manifest):
    me = recruiter_client.me()
    assert me["id"] == manifest["recruiter_id"]
    assert me["role"] == "recruiter"


def test_nurse_me_role(nurse_client, manifest):
    me = nurse_client.me()
    assert me["id"] == manifest["nurse_id"]
    assert me["role"] == "nurse"


def test_roles_never_cross(recruiter_client, nurse_client, manifest):
    r = recruiter_client.me()
    n = nurse_client.me()
    assert r["role"] != n["role"]
    assert r["id"] != n["id"]
    assert r["role"] == "recruiter"
    assert n["role"] == "nurse"


def test_websocket_auth_valid(base_url, nurse_token, manifest):
    run_async(ws_connect_once(base_url, manifest["nurse_id"], nurse_token))


def test_websocket_auth_rejected(base_url, manifest):
    run_async(ws_auth_rejected(base_url, manifest["nurse_id"], "invalid.jwt.token"))


def test_reconnect_auth(base_url, nurse_token, manifest):
    from tests.helpers.ws_client import ws_reconnect_cycle

    run_async(ws_reconnect_cycle(base_url, manifest["nurse_id"], nurse_token, cycles=3))


def test_me_json_serializable(recruiter_client, nurse_client):
    assert_json_serializable(recruiter_client.me())
    assert_json_serializable(nurse_client.me())
