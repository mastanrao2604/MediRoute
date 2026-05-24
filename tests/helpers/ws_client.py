"""WebSocket + reconnect simulation helpers."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional
from urllib.parse import quote

import websockets


def ws_url(base_url: str, user_id: int, token: str) -> str:
    ws_base = base_url.replace("https://", "wss://").replace("http://", "ws://")
    return f"{ws_base}/ws/{user_id}?token={quote(token)}"


async def ws_connect_once(base_url: str, user_id: int, token: str, timeout: float = 10.0) -> None:
    url = ws_url(base_url, user_id, token)
    async with websockets.connect(url, open_timeout=timeout, close_timeout=timeout) as ws:
        await ws.send(json.dumps({"type": "ping"}))
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        msg = json.loads(raw)
        if msg.get("type") != "pong":
            raise AssertionError(f"Expected pong, got {msg}")


async def ws_reconnect_cycle(
    base_url: str,
    user_id: int,
    token: str,
    cycles: int = 3,
) -> None:
    """Connect → ping → disconnect → repeat. Validates auth survives reconnect."""
    url = ws_url(base_url, user_id, token)
    for i in range(cycles):
        async with websockets.connect(url, open_timeout=10) as ws:
            await ws.send(json.dumps({"type": "ping"}))
            pong = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert pong.get("type") == "pong", f"cycle {i}: bad pong {pong}"


async def ws_auth_rejected(base_url: str, user_id: int, bad_token: str) -> None:
    url = ws_url(base_url, user_id, bad_token)
    try:
        async with websockets.connect(url, open_timeout=5) as ws:
            await ws.recv()
        raise AssertionError("Expected WS auth rejection")
    except websockets.exceptions.ConnectionClosedError as exc:
        if exc.code not in (4001, 1006, 1008):
            raise AssertionError(f"Unexpected close code {exc.code}") from exc
    except websockets.exceptions.InvalidStatus as exc:
        # Handshake rejected before WS upgrade (common for invalid JWT)
        if getattr(exc, "response", None) and exc.response.status_code not in (403, 401):
            raise AssertionError(f"Unexpected HTTP status {exc.response.status_code}") from exc


async def ws_duplicate_connection(
    base_url: str,
    user_id: int,
    token: str,
) -> None:
    """Second connection replaces first — no crash."""
    url = ws_url(base_url, user_id, token)
    ws1 = await websockets.connect(url, open_timeout=10)
    try:
        ws2 = await websockets.connect(url, open_timeout=10)
        try:
            await ws2.send(json.dumps({"type": "ping"}))
            pong = json.loads(await asyncio.wait_for(ws2.recv(), timeout=5))
            assert pong.get("type") == "pong"
        finally:
            await ws2.close()
    finally:
        await ws1.close()


def run_async(coro):
    return asyncio.run(coro)
