import axios from 'axios';
import { mlog } from '../utils/mobileLogger';

// For Capacitor APK builds: VITE_API_URL is set in the local .env file to the
// production backend URL (https://mediroute-8az0.onrender.com).
//
// For web builds on Render (no .env file): VITE_API_URL is undefined, so we
// fall back to window.location.origin — which IS the backend host when the
// frontend is served from the same domain. This means API calls are same-origin
// (no CORS overhead) and no additional env var config is needed on Render.
const BASE_URL =
  import.meta.env.VITE_API_URL ??
  (typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8000');

const api = axios.create({
  baseURL: BASE_URL,
  // 12 second hard timeout per request. Without this, a request to a sleeping
  // Render backend hangs indefinitely and freezes the app UI.
  timeout: 12000,
  headers: { 'Content-Type': 'application/json' },
});

// ── Request interceptor: attach access token ──────────────────────────────────
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('mediroute_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// ── Refresh token queue: coalesce concurrent 401s into one refresh call ───────
let isRefreshing = false;
let failedQueue = [];

function processQueue(error, token = null) {
  failedQueue.forEach(({ resolve, reject }) => {
    if (error) reject(error);
    else resolve(token);
  });
  failedQueue = [];
}

function clearSession() {
  localStorage.removeItem('mediroute_token');
  localStorage.removeItem('mediroute_refresh_token');
  localStorage.removeItem('mediroute_user');
}

// ── Response interceptor: auto-refresh on 401 ────────────────────────────────
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;

    // Log non-401 API failures (network errors, 5xx, 429, etc.)
    const status = error.response?.status;
    if (status !== 401) {
      const method = originalRequest?.method?.toUpperCase() ?? '?';
      const url    = originalRequest?.url ?? '?';
      mlog('api', 'request_error', { method, url, status: status ?? null, code: error.code ?? null });
    }

    // Only handle 401s that haven't already been retried
    if (status !== 401 || originalRequest._retry) {
      return Promise.reject(error);
    }

    // If the 401 came from /auth/refresh itself, the token is truly invalid — force logout
    if (originalRequest.url?.includes('/auth/refresh')) {
      clearSession();
      window.location.href = '/login';
      return Promise.reject(error);
    }

    const refreshToken = localStorage.getItem('mediroute_refresh_token');
    if (!refreshToken) {
      // No refresh token — session truly expired, force login
      clearSession();
      window.location.href = '/login';
      return Promise.reject(error);
    }

    // Another request is already refreshing — queue this one
    if (isRefreshing) {
      return new Promise((resolve, reject) => {
        failedQueue.push({ resolve, reject });
      }).then((newToken) => {
        originalRequest.headers.Authorization = `Bearer ${newToken}`;
        return api(originalRequest);
      });
    }

    originalRequest._retry = true;
    isRefreshing = true;

    try {
      // Use plain axios (not `api`) to avoid interceptor recursion on the refresh call
      const res = await axios.post(`${BASE_URL}/auth/refresh`, {
        refresh_token: refreshToken,
      });
      const newToken = res.data.access_token;
      localStorage.setItem('mediroute_token', newToken);
      api.defaults.headers.common.Authorization = `Bearer ${newToken}`;
      processQueue(null, newToken);
      originalRequest.headers.Authorization = `Bearer ${newToken}`;
      return api(originalRequest);
    } catch (refreshError) {
      processQueue(refreshError, null);
      clearSession();
      window.location.href = '/login';
      return Promise.reject(refreshError);
    } finally {
      isRefreshing = false;
    }
  }
);

export default api;
