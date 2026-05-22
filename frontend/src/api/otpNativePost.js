/**
 * OTP API posts on Capacitor Android/iOS — native HTTP bypasses WebView CORS (ERR_NETWORK).
 * Web/browser builds continue to use axios via api.post.
 */
import { Capacitor, CapacitorHttp } from '@capacitor/core';
import api, { API_BASE_URL } from './axios';

function parseBody(data) {
  if (data == null || data === '') return {};
  if (typeof data === 'object') return data;
  try {
    return JSON.parse(data);
  } catch {
    return { detail: String(data) };
  }
}

function nativeError(nativeRes) {
  const err = new Error('Request failed');
  err.response = { status: nativeRes.status, data: parseBody(nativeRes.data) };
  err.code = nativeRes.status >= 500 ? 'ERR_BAD_RESPONSE' : 'ERR_BAD_REQUEST';
  return err;
}

/**
 * @param {'/auth/send-otp'|'/auth/verify-otp'} path
 * @param {object} body
 * @param {number} [timeoutMs]
 */
export async function otpPost(path, body, timeoutMs = 45000) {
  if (!Capacitor.isNativePlatform()) {
    return api.post(path, body, { timeout: timeoutMs });
  }

  const url = `${API_BASE_URL}${path}`;
  const nativeRes = await CapacitorHttp.post({
    url,
    headers: { 'Content-Type': 'application/json' },
    data: body,
  });

  const data = parseBody(nativeRes.data);
  if (nativeRes.status < 200 || nativeRes.status >= 300) {
    throw nativeError(nativeRes);
  }
  return { status: nativeRes.status, data };
}

/** GET /auth/me after OTP verify (same native path as send-otp). */
export async function authGet(path, headers = {}, timeoutMs = 20000) {
  if (!Capacitor.isNativePlatform()) {
    return api.get(path, { headers, timeout: timeoutMs });
  }

  const nativeRes = await CapacitorHttp.get({
    url: `${API_BASE_URL}${path}`,
    headers,
  });

  const data = parseBody(nativeRes.data);
  if (nativeRes.status < 200 || nativeRes.status >= 300) {
    throw nativeError(nativeRes);
  }
  return { status: nativeRes.status, data };
}
