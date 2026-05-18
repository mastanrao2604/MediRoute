import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { GoogleLogin } from '@react-oauth/google';
import { Capacitor } from '@capacitor/core';
import { GoogleAuth } from '@codetrix-studio/capacitor-google-auth';
import api from '../api/axios';
import { useAuth } from '../context/AuthContext';
import { navigateAfterLogin } from '../utils/authNav';
import { validateIndianPhone, stripCountryCode } from '../utils/phoneValidation';
import { mlog, mlogError } from '../utils/mobileLogger';

// True only inside the Android/iOS Capacitor shell — false in browser.
const IS_NATIVE = Capacitor.isNativePlatform();

export default function Login() {
  const [phone, setPhone] = useState('');
  const [phoneError, setPhoneError] = useState('');
  const [phoneTouched, setPhoneTouched] = useState(false);
  const [loading, setLoading] = useState(false);
  const [googleLoading, setGoogleLoading] = useState(false);
  const [googleStatusMsg, setGoogleStatusMsg] = useState('Signing in with Google…');
  const [error, setError] = useState('');
  const [showPhoneForm, setShowPhoneForm] = useState(false);
  const navigate = useNavigate();
  const { login } = useAuth();

  function handlePhoneChange(e) {
    // Strip non-digits (but allow +91 prefix during typing)
    const raw = e.target.value.replace(/[^\d+]/g, '');
    setPhone(raw);
    if (phoneTouched || raw.length > 0) {
      const result = validateIndianPhone(raw);
      setPhoneError(result.valid ? '' : result.error);
    }
  }

  function handlePhoneBlur() {
    setPhoneTouched(true);
    if (phone) {
      const result = validateIndianPhone(phone);
      setPhoneError(result.valid ? '' : result.error);
    }
  }

  const phoneValidation = validateIndianPhone(phone);
  const isPhoneValid = phoneValidation.valid;

  async function handleSendOTP(e) {
    e.preventDefault();
    setPhoneTouched(true);
    const validation = validateIndianPhone(phone);
    if (!validation.valid) {
      setPhoneError(validation.error);
      return;
    }
    setError('');
    setLoading(true);
    mlog('otp', 'send_start');
    try {
      console.log('[OTP] sending to', validation.cleaned);
      const res = await api.post('/auth/send-otp', { phone: validation.cleaned });
      const devMode = !!res.data.dev_otp;
      console.log('[OTP] response status', res.status, 'dev_otp present:', devMode);
      mlog('otp', 'send_success', { dev_mode: devMode });
      navigate('/verify-otp', { state: { phone: validation.cleaned, devOtp: res.data.dev_otp } });
    } catch (err) {
      console.error('[OTP] send-otp error:', err?.message, err?.response?.status, err?.response?.data);
      mlogError('otp', 'send_fail', err);
      const raw = err?.response?.data?.detail;
      setError(
        typeof raw === 'string' ? raw
          : Array.isArray(raw) ? raw.map((e) => e.msg || String(e)).join('. ')
          : 'Failed to send OTP. Try again.',
      );
    } finally {
      setLoading(false);
    }
  }

  async function handleGoogleSuccess(credentialResponse) {
    setGoogleLoading(true);
    setGoogleStatusMsg('Signing in with Google…');
    setError('');
    try {
      // Use a 30s timeout — Render free-tier cold starts take 30-60s, far exceeding
      // the 12s global default. /auth/google is idempotent so a single retry is safe.
      let res;
      try {
        res = await api.post('/auth/google', { token: credentialResponse.credential }, { timeout: 30000 });
      } catch (firstErr) {
        const isTimeout = firstErr?.code === 'ECONNABORTED' || (firstErr?.message || '').includes('timeout');
        if (!isTimeout) throw firstErr;
        setGoogleStatusMsg('Server is waking up, retrying…');
        await new Promise((r) => setTimeout(r, 1500));
        setGoogleStatusMsg('Connecting securely…');
        res = await api.post('/auth/google', { token: credentialResponse.credential }, { timeout: 40000 });
      }
      if (res.data.phone_verification_required) {
        navigate('/link-phone', { state: { googleSessionToken: res.data.google_session_token } });
      } else {
        // Backend is already warm — 20s is more than enough for /auth/me.
        const meRes = await api.get('/auth/me', {
          headers: { Authorization: `Bearer ${res.data.access_token}` },
          timeout: 20000,
        });
        login(res.data.access_token, meRes.data, res.data.refresh_token);
        navigateAfterLogin(meRes.data, navigate);
      }
    } catch (err) {
      const isTimeout = err?.code === 'ECONNABORTED' || (err?.message || '').includes('timeout');
      if (isTimeout) {
        setError('Server is starting up. Please wait a moment and try again.');
      } else {
        const raw = err?.response?.data?.detail;
        setError(
          typeof raw === 'string' ? raw
            : Array.isArray(raw) ? raw.map((e) => e.msg || String(e)).join('. ')
            : 'Google sign-in failed. Try again.',
        );
      }
    } finally {
      setGoogleLoading(false);
      setGoogleStatusMsg('Signing in with Google…');
    }
  }

  // Native (Capacitor APK) path — uses Android Google Sign-In SDK via SHA-1.
  // This bypasses the iframe-based GIS button which doesn't work in WebView.
  async function handleNativeGoogleSignIn() {
    setGoogleLoading(true);
    setError('');
    try {
      // v3.x RC requires explicit initialize() before signIn() — this sets up
      // the native GoogleSignInClient. Safe to call on every attempt (idempotent).
      await GoogleAuth.initialize({
        clientId: import.meta.env.VITE_GOOGLE_CLIENT_ID,
        scopes: ['profile', 'email'],
        grantOfflineAccess: true,
      });
      const googleUser = await GoogleAuth.signIn();
      // Try both idToken locations — plugin v3.x RC puts it in both places
      const idToken = googleUser?.authentication?.idToken || googleUser?.idToken;
      if (!idToken) throw new Error(
        `No ID token returned. Auth: ${!!googleUser?.authentication}, ` +
        `idToken in auth: ${googleUser?.authentication?.idToken}, ` +
        `idToken top-level: ${googleUser?.idToken}`
      );
      // Reuse the exact same backend endpoint as the web flow.
      // 30s timeout for first attempt — covers Render cold start.
      // /auth/google is idempotent so a single cold-start retry is safe.
      let res;
      try {
        res = await api.post('/auth/google', { token: idToken }, { timeout: 30000 });
      } catch (firstErr) {
        const isTimeout = firstErr?.code === 'ECONNABORTED' || (firstErr?.message || '').includes('timeout');
        if (!isTimeout) throw firstErr;
        setGoogleStatusMsg('Server is waking up, retrying…');
        await new Promise((r) => setTimeout(r, 1500));
        setGoogleStatusMsg('Connecting securely…');
        res = await api.post('/auth/google', { token: idToken }, { timeout: 40000 });
      }
      if (res.data.phone_verification_required) {
        navigate('/link-phone', { state: { googleSessionToken: res.data.google_session_token } });
      } else {
        // Backend is already warm — 20s is more than enough for /auth/me.
        const meRes = await api.get('/auth/me', {
          headers: { Authorization: `Bearer ${res.data.access_token}` },
          timeout: 20000,
        });
        login(res.data.access_token, meRes.data, res.data.refresh_token);
        navigateAfterLogin(meRes.data, navigate);
      }
    } catch (err) {
      // 12501 = user cancelled the sign-in sheet — silent dismiss
      if (err?.code === 12501 || String(err).includes('12501')) {
        setGoogleLoading(false);
        return;
      }
      const isTimeout = err?.code === 'ECONNABORTED' || (err?.message || '').includes('timeout');
      if (isTimeout) {
        setError('Server is starting up. Please wait a moment and try again.');
      } else {
        // Pydantic v2 may return detail as an array — coerce to string to avoid React Error #31.
        const raw = err?.response?.data?.detail;
        setError(
          typeof raw === 'string' ? raw
            : Array.isArray(raw) ? raw.map((e) => e.msg || String(e)).join('. ')
            : err?.message || String(err) || 'Google sign-in failed. Please try again.',
        );
      }
    } finally {
      setGoogleLoading(false);
      setGoogleStatusMsg('Signing in with Google…');
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-b from-indigo-50 to-white flex flex-col items-center justify-center px-4 py-8">
      <div className="w-full max-w-sm">

        {/* ── Logo ── */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center gap-0.5 mb-3">
            <span className="text-indigo-600 font-extrabold text-4xl tracking-tight">Medi</span>
            <span className="text-green-500 font-extrabold text-4xl tracking-tight">Route</span>
          </div>
          <p className="text-gray-500 text-sm font-medium">Real-Time Healthcare Staffing</p>
        </div>

        <div className="bg-white rounded-2xl shadow-md border border-gray-100 overflow-hidden">

          {/* ── Header ── */}
          <div className="px-6 pt-6 pb-4">
            <h2 className="text-xl font-bold text-gray-900">Welcome back</h2>
            <p className="text-sm text-gray-500 mt-0.5">Sign in to continue</p>
          </div>

          {/* ── PRIMARY: Google Login ── */}
          <div className="px-6 pb-2">
            <div className={`flex flex-col items-center gap-2 ${googleLoading ? 'pointer-events-none' : ''}`}>
              {googleLoading ? (
                <div className="w-full flex items-center justify-center gap-3 bg-indigo-600 text-white font-semibold py-3.5 rounded-xl">
                  <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  <span>{googleStatusMsg}</span>
                </div>
              ) : IS_NATIVE ? (
                /* ── Native APK: uses Android Google Sign-In SDK (SHA-1 based, no iframe) ── */
                <button
                  type="button"
                  onClick={handleNativeGoogleSignIn}
                  className="w-full flex items-center justify-center gap-3 bg-white border border-gray-300 hover:border-gray-400 hover:bg-gray-50 active:bg-gray-100 text-gray-700 font-semibold py-3.5 rounded-xl transition-all shadow-sm"
                >
                  {/* Official Google "G" logo */}
                  <svg width="20" height="20" viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">
                    <path fill="#4285F4" d="M44.5 20H24v8.5h11.7C34.1 33.9 29.6 37 24 37c-7.2 0-13-5.8-13-13s5.8-13 13-13c3.1 0 6 1.1 8.1 3l6.4-6.4C34.6 4.1 29.6 2 24 2 11.3 2 1 12.3 1 25s10.3 23 23 23c13.2 0 22-9.2 22-22.2 0-1.5-.2-2.5-.5-3.8z"/>
                    <path fill="#34A853" d="M6.3 14.7l7 5.1C15.1 16.1 19.2 13 24 13c3.1 0 6 1.1 8.1 3l6.4-6.4C34.6 4.1 29.6 2 24 2c-7.6 0-14.2 4.4-17.7 10.7z"/>
                    <path fill="#FBBC05" d="M24 46c5.5 0 10.1-1.8 13.5-4.9l-6.6-5.4C29.1 37.5 26.7 38 24 38c-5.6 0-10.3-3.5-12.1-8.3l-7 5.4C8.1 41.8 15.5 46 24 46z"/>
                    <path fill="#EA4335" d="M44.5 20H24v8.5h11.7c-.8 2.9-2.9 5.4-5.6 7.1l6.6 5.4C40.7 38.1 44.5 32.3 44.5 24c0-1.5-.2-2.5-.5-4z"/>
                  </svg>
                  Continue with Google
                </button>
              ) : (
                /* ── Web browser: renders the GIS iframe button (requires accounts.google.com) ── */
                <div className="w-full flex justify-center [&>div]:w-full [&>div>div]:w-full">
                  <GoogleLogin
                    onSuccess={handleGoogleSuccess}
                    onError={() => setError('Google sign-in was cancelled or failed.')}
                    text="continue_with"
                    shape="rectangular"
                    theme="filled_blue"
                    size="large"
                    logo_alignment="left"
                    width="320"
                    useOneTap={false}
                  />
                </div>
              )}
              <p className="text-xs text-gray-400 text-center">
                Fastest way in &nbsp;•&nbsp; No password needed
              </p>
            </div>
          </div>

          {/* ── Error ── */}
          {error && (
            <div className="px-6 pt-1 pb-2">
              <p className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded-lg">{error}</p>
            </div>
          )}

          {/* ── Divider ── */}
          <div className="flex items-center gap-3 px-6 py-4">
            <div className="flex-1 h-px bg-gray-200" />
            <span className="text-xs text-gray-400 font-medium whitespace-nowrap">or use phone number</span>
            <div className="flex-1 h-px bg-gray-200" />
          </div>

          {/* ── SECONDARY: Phone OTP ── */}
          <div className="px-6 pb-6">
            {!showPhoneForm ? (
              <button
                type="button"
                onClick={() => setShowPhoneForm(true)}
                className="w-full border border-gray-300 hover:border-indigo-400 hover:bg-indigo-50 text-gray-600 hover:text-indigo-700 font-medium py-3 rounded-xl transition-all text-sm flex items-center justify-center gap-2"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.69 12a19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 3.6 1.2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 8.9a16 16 0 0 0 6 6l.76-.76a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>
                </svg>
                Continue with Phone OTP
              </button>
            ) : (
              <form onSubmit={handleSendOTP} className="flex flex-col gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-600 mb-1.5">
                    Phone Number
                  </label>
                  <input
                    type="tel"
                    inputMode="numeric"
                    value={phone}
                    onChange={handlePhoneChange}
                    onBlur={handlePhoneBlur}
                    placeholder="9876543210"
                    maxLength={13}
                    className={`w-full border rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:border-transparent transition ${
                      phoneTouched && phoneError
                        ? 'border-red-400 focus:ring-red-300 bg-red-50'
                        : phoneTouched && isPhoneValid
                        ? 'border-green-400 focus:ring-green-300'
                        : 'border-gray-300 focus:ring-indigo-400'
                    }`}
                    autoFocus
                  />
                  {phoneTouched && phoneError && (
                    <p className="text-xs text-red-600 mt-1.5 flex items-center gap-1">
                      <svg className="w-3.5 h-3.5 flex-shrink-0" viewBox="0 0 20 20" fill="currentColor">
                        <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                      </svg>
                      {phoneError}
                    </p>
                  )}
                  {phoneTouched && isPhoneValid && (
                    <p className="text-xs text-green-600 mt-1.5 flex items-center gap-1">
                      <svg className="w-3.5 h-3.5 flex-shrink-0" viewBox="0 0 20 20" fill="currentColor">
                        <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                      </svg>
                      Looks good!
                    </p>
                  )}
                </div>
                <button
                  type="submit"
                  disabled={loading || googleLoading || (phoneTouched && !isPhoneValid)}
                  className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 disabled:cursor-not-allowed text-white font-semibold py-3 rounded-xl transition-colors text-sm"
                >
                  {loading ? 'Sending…' : 'Send OTP'}
                </button>
                <button
                  type="button"
                  onClick={() => { setShowPhoneForm(false); setError(''); setPhone(''); setPhoneError(''); setPhoneTouched(false); }}
                  className="text-xs text-gray-400 hover:text-gray-600 text-center transition-colors"
                >
                  ← Back
                </button>
              </form>
            )}
          </div>
        </div>

        <p className="text-center text-xs text-gray-400 mt-6">
          By continuing, you agree to our Terms of Service
        </p>
      </div>
    </div>
  );
}
