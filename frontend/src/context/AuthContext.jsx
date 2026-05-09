import { createContext, useContext, useState, useEffect } from 'react';
import api from '../api/axios';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(null);
  const [loading, setLoading] = useState(true);

  // On mount: restore session from stored tokens.
  // The axios interceptor transparently refreshes an expired access token
  // using the stored refresh token, so we just call /auth/me and let it handle itself.
  useEffect(() => {
    const storedToken = localStorage.getItem('mediroute_token');
    const storedRefresh = localStorage.getItem('mediroute_refresh_token');

    if (!storedToken && !storedRefresh) {
      // No session at all — go straight to loading=false (stay on login)
      setLoading(false);
      return;
    }

    api
      .get('/auth/me')
      .then((res) => {
        // Token may have been silently refreshed by the interceptor — read latest
        const latestToken = localStorage.getItem('mediroute_token');
        setToken(latestToken);
        setUser(res.data);
        localStorage.setItem('mediroute_user', JSON.stringify(res.data));
      })
      .catch(() => {
        // Interceptor already cleared tokens and redirected if refresh failed.
        // Just reset local state here.
        setToken(null);
        setUser(null);
      })
      .finally(() => setLoading(false));
  }, []);

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
    <AuthContext.Provider value={{ user, token, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
