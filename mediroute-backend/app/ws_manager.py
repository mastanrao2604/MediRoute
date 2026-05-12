"""
WebSocket Connection Manager — Phase 1 in-memory implementation.

Manages active WebSocket connections keyed by user_id.
Phase 1: in-process dict (single Render instance).
Phase 2: replace send() body with Redis PUBLISH — zero changes to callers.

Memory leak prevention:
  - Connections removed from dict on disconnect (disconnect() call or exception)
  - send() silently drops stale connections and removes them from the dict
  - No unbounded growth — one entry per connected user
  - Stale connection detection: track last_pong_at per connection.
    Janitor calls prune_stale() every 30s to close truly-dead Android sockets.

Stale socket problem on Android:
  Android Doze mode and NAT/carrier middleboxes silently drop TCP connections
  without sending a FIN/RST. The server still holds the socket in ESTABLISHED
  state and the dict entry leaks. We detect this by tracking the last time
  each client responded to a server-sent ping. Connections silent for
  WS_STALE_TIMEOUT_SEC are force-closed.
"""
import asyncio
import json
import logging
import time

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# Connections silent for longer than this are considered dead (Android-safe: 90s)
WS_STALE_TIMEOUT_SEC = 90


class ConnectionManager:
    """Asyncio-safe in-memory WebSocket connection registry with stale-socket eviction."""

    def __init__(self):
        # user_id → WebSocket. One active connection per user.
        self._connections: dict[int, WebSocket] = {}
        # user_id → monotonic time of last received ping/pong from client.
        # Populated on connect and updated via record_pong().
        self._last_pong_at: dict[int, float] = {}

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        """Accept WebSocket and register. Replaces any stale previous connection."""
        await websocket.accept()
        # Close stale connection if user reconnects (e.g. after network blip)
        old = self._connections.get(user_id)
        if old and old is not websocket:
            try:
                await old.close(code=1001)  # 1001 = going away (server-side close)
            except Exception:
                pass  # already closed — ignore
        self._connections[user_id] = websocket
        self._last_pong_at[user_id] = time.monotonic()  # mark alive on connect
        logger.debug("[WS] user %d connected (total: %d)", user_id, len(self._connections))

    def disconnect(self, user_id: int) -> None:
        """Remove connection from registry. Safe to call multiple times."""
        self._connections.pop(user_id, None)
        self._last_pong_at.pop(user_id, None)
        logger.debug("[WS] user %d disconnected (total: %d)", user_id, len(self._connections))

    def record_pong(self, user_id: int) -> None:
        """Record that a ping was received from the client. Resets stale-timeout."""
        if user_id in self._connections:
            self._last_pong_at[user_id] = time.monotonic()

    async def prune_stale(self, threshold_sec: float = WS_STALE_TIMEOUT_SEC) -> int:
        """
        Close and remove connections that have been silent for > threshold_sec.
        Called by the janitor every 30s. Returns count of pruned connections.

        This handles dead Android sockets that never sent a FIN/RST — they stay
        in the dict forever without this cleanup.
        """
        now = time.monotonic()
        stale_ids = [
            uid for uid, last in list(self._last_pong_at.items())
            if now - last > threshold_sec
        ]
        for uid in stale_ids:
            ws = self._connections.get(uid)
            if ws:
                try:
                    await ws.close(code=1001)
                except Exception:
                    pass
            self._connections.pop(uid, None)
            self._last_pong_at.pop(uid, None)

        if stale_ids:
            logger.info("[WS] pruned %d stale connections (threshold: %ds)", len(stale_ids), int(threshold_sec))
        return len(stale_ids)

    async def send(self, user_id: int, payload: dict) -> bool:
        """
        Send JSON message to a specific user. Returns True if delivered.

        Phase 2 upgrade path:
          Replace this body with:
            await redis.publish(f"ws:user:{user_id}", json.dumps(payload))
            return True
          Zero changes to callers.
        """
        ws = self._connections.get(user_id)
        if ws is None:
            return False
        try:
            await ws.send_text(json.dumps(payload))
            return True
        except Exception as exc:
            logger.warning("[WS] send failed for user %d: %s", user_id, exc)
            # Remove stale connection — it will reconnect
            self.disconnect(user_id)
            return False

    async def broadcast(self, user_ids: list[int], payload: dict) -> int:
        """Send to multiple users concurrently. Returns count of successful deliveries."""
        if not user_ids:
            return 0
        results = await asyncio.gather(
            *[self.send(uid, payload) for uid in user_ids],
            return_exceptions=True,
        )
        return sum(1 for r in results if r is True)

    def is_connected(self, user_id: int) -> bool:
        return user_id in self._connections

    @property
    def connection_count(self) -> int:
        return len(self._connections)


# Global singleton — import this everywhere
ws_manager = ConnectionManager()
