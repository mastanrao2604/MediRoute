import axios from 'axios';

const BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

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

    // Only handle 401s that haven't already been retried
    if (error.response?.status !== 401 || originalRequest._retry) {
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
