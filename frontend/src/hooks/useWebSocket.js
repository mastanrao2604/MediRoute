/**
 * useWebSocket — persistent WebSocket connection with auto-reconnect.
 *
 * Features:
 *  - Connects to /ws/{userId}?token={jwt}
 *  - Exponential backoff reconnect: 1s → 2s → 4s → 8s → 30s max
 *  - Client-side ping every 25s to keep connection alive through Android Doze
 *  - On reconnect: fetches /dispatch/offers/pending to recover missed offers
 *  - Cleans up on unmount / logout
 *
 * Usage:
 *   const { isConnected } = useWebSocket(user, token, onMessage);
 */
import { useEffect, useRef, useCallback, useState } from 'react';
import api from '../api/axios';

const BASE_URL =
  import.meta.env.VITE_API_URL ??
  (typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8000');

const WS_BASE = BASE_URL.replace(/^http/, 'ws');

const MAX_BACKOFF_SEC = 30;
const PING_INTERVAL_MS = 25_000;

export function useWebSocket(user, token, onMessage) {
  const wsRef = useRef(null);
  const reconnectTimer = useRef(null);
  const pingTimer = useRef(null);
  const backoffRef = useRef(1);
  const mountedRef = useRef(true);
  const connectRef = useRef(null); // Task 16: ref to latest connect fn (stale closure fix)
  const [connected, setConnected] = useState(false);

  const clearTimers = () => {
    if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
    if (pingTimer.current) clearInterval(pingTimer.current);
  };

  const fetchMissedOffers = useCallback(async () => {
    try {
      const res = await api.get('/dispatch/offers/pending');
      const offers = res.data?.offers || [];
      for (const offer of offers) {
        onMessage({ type: 'dispatch_offer', ...offer });
      }
    } catch {
      // Non-critical — ignore
    }
  }, [onMessage]);

  const connect = useCallback(() => {
    if (!user?.id || !token || !mountedRef.current) return;

    // Close existing connection if any
    if (wsRef.current) {
      wsRef.current.onclose = null; // prevent reconnect loop
      wsRef.current.close();
    }

    const url = `${WS_BASE}/ws/${user.id}?token=${encodeURIComponent(token)}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      if (!mountedRef.current) { ws.close(); return; }
      backoffRef.current = 1; // reset backoff on success
      setConnected(true);

      // Start keepalive ping
      clearInterval(pingTimer.current);
      pingTimer.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'ping' }));
        }
      }, PING_INTERVAL_MS);

      // Fetch any offers missed during the disconnect
      fetchMissedOffers();
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type !== 'pong') {
          onMessage(data);
        }
      } catch {
        // ignore malformed messages
      }
    };

    ws.onclose = (event) => {
      clearInterval(pingTimer.current);
      setConnected(false);
      if (!mountedRef.current) return;

      // 4001 = auth failure — don't reconnect
      if (event.code === 4001) {
        console.warn('[WS] auth rejected — not reconnecting');
        return;
      }

      // Task 7: ±20% jitter on reconnect delay to spread thundering-herd reconnects
      const jitter = 0.8 + Math.random() * 0.4;
      const delay = Math.min(backoffRef.current, MAX_BACKOFF_SEC) * 1000 * jitter;
      backoffRef.current = Math.min(backoffRef.current * 2, MAX_BACKOFF_SEC);
      reconnectTimer.current = setTimeout(() => connectRef.current?.(), delay);
    };

    ws.onerror = () => {
      // onerror is always followed by onclose — let onclose handle reconnect
    };
  }, [user?.id, token, onMessage, fetchMissedOffers]);

  // Task 16: Keep connectRef current so ws.onclose closure always calls the latest version
  connectRef.current = connect;

  useEffect(() => {
    mountedRef.current = true;
    if (user?.id && token) {
      connect();
    }
    return () => {
      mountedRef.current = false;
      clearTimers();
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
      }
    };
  }, [user?.id, token]); // reconnect if user/token changes

  // App-resume reconnect: when Android brings the app back to foreground after
  // Doze / NAT timeout killed the WS connection, reconnect immediately instead
  // of waiting for the next backoff timer to fire.
  useEffect(() => {
    if (!user?.id || !token) return;
    let listener;
    (async () => {
      try {
        const { App } = await import('@capacitor/app');
        listener = await App.addListener('appStateChange', ({ isActive }) => {
          if (!isActive) return;
          const ws = wsRef.current;
          const isDead = !ws
            || ws.readyState === WebSocket.CLOSED
            || ws.readyState === WebSocket.CLOSING;
          if (isDead && mountedRef.current) {
            // Cancel any pending backoff timer and reconnect immediately
            clearTimeout(reconnectTimer.current);
            backoffRef.current = 1;
            connectRef.current?.();
          }
        });
      } catch {
        // Not in Capacitor (browser / PWA) — skip
      }
    })();
    return () => { listener?.remove?.(); };
  }, [user?.id, token]);

  return {
    isConnected: connected,
    // Legacy: keep wsRef-based value as fallback for synchronous checks
    isConnectedSync: wsRef.current?.readyState === WebSocket.OPEN,
  };
}
