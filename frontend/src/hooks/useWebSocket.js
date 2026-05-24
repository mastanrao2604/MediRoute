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
import { mlog, mlogError } from '../utils/mobileLogger';

const BASE_URL =
  import.meta.env.VITE_API_URL ??
  (typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8000');

const WS_BASE = BASE_URL.replace(/^http/, 'ws');

const MAX_BACKOFF_SEC = 30;
const PING_INTERVAL_MS = 25_000;

export function useWebSocket(user, token, onMessage, onAuthError) {
  const wsRef = useRef(null);
  const reconnectTimer = useRef(null);
  const pingTimer = useRef(null);
  const backoffRef = useRef(1);
  const mountedRef = useRef(true);
  const connectRef = useRef(null); // Task 16: ref to latest connect fn (stale closure fix)
  const connIdRef = useRef(null);
  const lastClosedAtRef = useRef(null);
  const hadConnectedRef = useRef(false);
  const [connected, setConnected] = useState(false);

  // Rapid-failure detection: if the WS closes within RAPID_FAIL_WINDOW_MS of
  // the connection attempt (i.e. the server rejected the upgrade with HTTP 403),
  // we suspect the token is expired. After RAPID_FAIL_THRESHOLD consecutive
  // rapid failures, we call onAuthError() and pause reconnection so the auth
  // layer can refresh the token before we retry.
  const RAPID_FAIL_WINDOW_MS = 2000;
  const RAPID_FAIL_THRESHOLD = 3;
  const connectAttemptTime = useRef(null);
  const rapidFailCount = useRef(0);

  /** REST probe: distinguish expired token from server cold-start / network blip. */
  const probeAuthState = useCallback(async () => {
    try {
      await api.get('/auth/me', { timeout: 5000 });
      return 'valid';
    } catch (err) {
      const status = err?.response?.status;
      if (status === 401 || status === 403) return 'invalid';
      return 'unknown';
    }
  }, []);

  const clearTimers = () => {
    if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
    if (pingTimer.current) clearInterval(pingTimer.current);
  };

  const fetchMissedOffers = useCallback(async () => {
    try {
      const res = await api.get('/dispatch/offers/pending');
      const offers = res.data?.offers || [];
      if (offers.length > 0) {
        const sids = [...new Set(offers.map((o) => o.shift_id).filter((id) => id != null))];
        mlog('websocket', 'pending_offers_recovered', {
          cid: connIdRef.current,
          count: offers.length,
          sids: sids.slice(0, 10),
        });
      }
      for (const offer of offers) {
        onMessage({ type: 'dispatch_offer', ...offer });
      }
      window.dispatchEvent(new CustomEvent('mr-nurse-active-shift-refresh'));
    } catch (err) {
      mlogError('websocket', 'pending_offers_fetch_failed', err);
      window.dispatchEvent(new CustomEvent('mr-nurse-active-shift-refresh'));
    }
  }, [onMessage]);

  const connect = useCallback(() => {
    if (!user?.id || !token || !mountedRef.current) return;

    // Close existing connection if any
    if (wsRef.current) {
      wsRef.current.onclose = null; // prevent reconnect loop
      wsRef.current.close();
    }

    connectAttemptTime.current = Date.now();
    if (!connIdRef.current) {
      connIdRef.current = Math.random().toString(36).slice(2, 8);
    }
    const url = `${WS_BASE}/ws/${user.id}?token=${encodeURIComponent(token)}`;
    mlog('websocket', 'connecting', { user_id: user.id, cid: connIdRef.current, reconn: hadConnectedRef.current });
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      if (!mountedRef.current) { ws.close(); return; }
      backoffRef.current = 1; // reset backoff on success
      rapidFailCount.current = 0; // successful connection — reset failure counter
      setConnected(true);
      const gapMs = lastClosedAtRef.current ? Date.now() - lastClosedAtRef.current : null;
      mlog('websocket', hadConnectedRef.current ? 'reconnected' : 'connected', {
        cid: connIdRef.current,
        gap_ms: gapMs,
      });
      hadConnectedRef.current = true;
      lastClosedAtRef.current = null;

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
      lastClosedAtRef.current = Date.now();
      mlog('websocket', 'closed', {
        cid: connIdRef.current,
        code: event.code,
        clean: event.wasClean,
      });
      if (!mountedRef.current) return;

      // 4001 = explicit auth failure from server — don't reconnect
      if (event.code === 4001) {
        console.warn('[WS] auth rejected — not reconnecting');
        mlog('websocket', 'auth_rejected');
        onAuthError?.();
        return;
      }

      // Rapid-failure detection: HTTP 403 upgrade rejection arrives as code 1006
      // within milliseconds of the connect attempt (no onopen ever fired).
      // After RAPID_FAIL_THRESHOLD consecutive rapid failures we assume the token
      // is expired and call onAuthError() instead of spinning in a hot retry loop.
      const elapsed = connectAttemptTime.current
        ? Date.now() - connectAttemptTime.current
        : Infinity;
      if (elapsed < RAPID_FAIL_WINDOW_MS && !event.wasClean) {
        rapidFailCount.current += 1;
        if (rapidFailCount.current >= RAPID_FAIL_THRESHOLD) {
          rapidFailCount.current = 0;
          console.warn('[WS] repeated rapid failures — probing auth before reconnect');
          probeAuthState().then((authState) => {
            if (authState === 'invalid') {
              mlog('websocket', 'rapid_fail_auth_confirmed', { rapid_count: RAPID_FAIL_THRESHOLD });
              onAuthError?.();
            } else if (authState === 'valid') {
              mlog('websocket', 'rapid_fail_server_unreachable', { rapid_count: RAPID_FAIL_THRESHOLD });
            } else {
              mlog('websocket', 'rapid_fail_network', { rapid_count: RAPID_FAIL_THRESHOLD });
            }
            backoffRef.current = 4;
            reconnectTimer.current = setTimeout(() => connectRef.current?.(), 4000);
          });
          return;
        }
      } else {
        rapidFailCount.current = 0; // not a rapid failure — reset counter
      }

      // Task 7: ±20% jitter on reconnect delay to spread thundering-herd reconnects
      const jitter = 0.8 + Math.random() * 0.4;
      const delay = Math.min(backoffRef.current, MAX_BACKOFF_SEC) * 1000 * jitter;
      const nextBackoff = Math.min(backoffRef.current * 2, MAX_BACKOFF_SEC);
      mlog('websocket', 'reconnect_scheduled', {
        cid: connIdRef.current,
        delay_ms: Math.round(delay),
        next_backoff_sec: nextBackoff,
      });
      backoffRef.current = nextBackoff;
      reconnectTimer.current = setTimeout(() => connectRef.current?.(), delay);
    };

    ws.onerror = () => {
      // onerror is always followed by onclose — let onclose handle reconnect
    };
  }, [user?.id, token, onMessage, fetchMissedOffers, probeAuthState]);

  // Task 16: Keep connectRef current so ws.onclose closure always calls the latest version
  connectRef.current = connect;

  useEffect(() => {
    mountedRef.current = true;
    if (user?.id && token) {
      connect();
    }
    return () => {
      mountedRef.current = false;
      mlog('websocket', 'teardown', { cid: connIdRef.current });
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
            mlog('websocket', 'resume_reconnect', { cid: connIdRef.current });
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
