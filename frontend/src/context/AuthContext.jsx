import { createContext, useContext, useState, useEffect } from 'react';
import api from '../api/axios';
import { mlog } from '../utils/mobileLogger';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(null);
  const [loading, setLoading] = useState(true);

  // On mount: restore session using a cache-first strategy.
  //
  // WHY: Render free-tier backends cold-start in 30-60 seconds. With no axios
  // timeout the old approach blocked the entire app behind the loading spinner
  // for that entire duration on every app launch after the backend sleeps.
  //
  // STRATEGY:
  //   1. If a cached user exists in localStorage → restore immediately (<50ms)
  //      and set loading=false so the app renders at once.
  //   2. Call /auth/me in the background to validate + refresh the cached data.
  //   3. On 401/403 → session truly expired → clear everything and go to login.
  //   4. On network error → keep the cached state; the user stays on their
  //      dashboard and future API calls retry when the backend wakes up.
  useEffect(() => {
    const storedToken = localStorage.getItem('mediroute_token');
    const storedRefresh = localStorage.getItem('mediroute_refresh_token');

    if (!storedToken && !storedRefresh) {
      // No session at all — show login immediately
      setLoading(false);
      return;
    }

    // ── Step 1: cache-first restore ─────────────────────────────────────────
    // Parse the cached user synchronously so the app is visible in < 100ms.
    let cachedUser = null;
    try {
      const stored = localStorage.getItem('mediroute_user');
      if (stored) cachedUser = JSON.parse(stored);
    } catch { /* ignore malformed JSON */ }

    if (cachedUser) {
      // Unblock the UI immediately — background validation below will silently
      // update user state once the backend responds.
      setToken(storedToken);
      setUser(cachedUser);
      setLoading(false);
      mlog('auth', 'session_restored_from_cache', { role: cachedUser.role });
    }

    // ── Step 2: background validation ───────────────────────────────────────
    api
      .get('/auth/me')
      .then((res) => {
        const latestToken = localStorage.getItem('mediroute_token');
        setToken(latestToken);
        setUser(res.data);
        localStorage.setItem('mediroute_user', JSON.stringify(res.data));
        mlog('auth', 'session_validated', { role: res.data.role });
      })
      .catch((err) => {
        if (err?.response?.status === 401 || err?.response?.status === 403) {
          // Token truly revoked — log the user out
          mlog('auth', 'session_expired', { status: err.response.status });
          setToken(null);
          setUser(null);
          localStorage.removeItem('mediroute_token');
          localStorage.removeItem('mediroute_refresh_token');
          localStorage.removeItem('mediroute_user');
        }
        // Network errors / timeouts: keep cached state; don't log the user out
      })
      .finally(() => {
        // Only needed when there was no cached user (first-ever login path)
        if (!cachedUser) setLoading(false);
      });
  }, []);

  /**
   * Re-validate the session by calling /auth/me.
   * Used by WebSocket layer when it detects a suspected expired token.
   * On 401/403 → logs the user out. On success → updates token + user state.
   */
  async function revalidate() {
    try {
      const res = await api.get('/auth/me');
      const latestToken = localStorage.getItem('mediroute_token');
      setToken(latestToken);
      setUser(res.data);
      localStorage.setItem('mediroute_user', JSON.stringify(res.data));
    } catch (err) {
      if (err?.response?.status === 401 || err?.response?.status === 403) {
        setToken(null);
        setUser(null);
        localStorage.removeItem('mediroute_token');
        localStorage.removeItem('mediroute_refresh_token');
        localStorage.removeItem('mediroute_user');
      }
    }
  }

  /**
   * Call after a successful login.
   * @param {string} accessToken
   * @param {object} userData
   * @param {string|null} refreshToken
   */
  function login(accessToken, userData, refreshToken = null) {
    localStorage.setItem('mediroute_token', accessToken);
    localStorage.setItem('mediroute_user', JSON.stringify(userData));
    if (refreshToken) {
      localStorage.setItem('mediroute_refresh_token', refreshToken);
    }
    setToken(accessToken);
    setUser(userData);
  }

  /**
   * Revoke the refresh token server-side, then clear all local session data.
   * Works even when the access token is already expired.
   */
  async function logout() {
    mlog('auth', 'logout');
    const refreshToken = localStorage.getItem('mediroute_refresh_token');
    try {
      if (refreshToken) {
        await api.post('/auth/logout', { refresh_token: refreshToken });
      }
    } catch {
      // Ignore API errors — local logout always happens
    } finally {
      localStorage.removeItem('mediroute_token');
      localStorage.removeItem('mediroute_refresh_token');
      localStorage.removeItem('mediroute_user');
      setToken(null);
      setUser(null);
    }
  }

  return (
    <AuthContext.Provider value={{ user, token, loading, login, logout, revalidate }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
