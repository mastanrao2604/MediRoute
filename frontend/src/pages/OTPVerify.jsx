import { useState, useEffect } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { authGet, otpPost } from '../api/otpNativePost';
import { useAuth } from '../context/AuthContext';
import { navigateAfterLogin } from '../utils/authNav';
import { mlog, mlogError } from '../utils/mobileLogger';
import { formatApiErrorDetail } from '../utils/apiErrorMessage';
import { consumeStashedDevOtp, normalizeDevOtp, stashDevOtp } from '../utils/devOtpStorage';

const RESEND_COOLDOWN = 30; // seconds

export default function OTPVerify() {
  const { state } = useLocation();
  const phone = state?.phone || '';
  const devOtpHint = normalizeDevOtp(state?.devOtp);
  const navigate = useNavigate();
  const { login } = useAuth();

  const [otp, setOtp] = useState('');
  const [showDevAssist, setShowDevAssist] = useState(Boolean(devOtpHint));
  const [loading, setLoading] = useState(false);
  const [resending, setResending] = useState(false);
  const [error, setError] = useState('');
  const [resendTimer, setResendTimer] = useState(RESEND_COOLDOWN);

  // Re-apply whenever navigation state/session provides pilot/dev OTP (native WebViews may omit history.state briefly)
  useEffect(() => {
    const stored = consumeStashedDevOtp();
    const loaded = devOtpHint || stored;
    if (loaded) {
      setOtp(loaded);
      setShowDevAssist(true);
    }

    mlog('otp', 'verify_screen_open', {
      autofilled: Boolean(loaded || devOtpHint),
      from_storage_fallback: Boolean(loaded && !devOtpHint),
    });
  }, [phone, devOtpHint]);

  // Guard: if no phone in state, user navigated here directly — send them back
  useEffect(() => {
    if (!phone) navigate('/login', { replace: true });
  }, []);

  // Countdown timer
  useEffect(() => {
    if (resendTimer <= 0) return;
    const id = setTimeout(() => setResendTimer(t => t - 1), 1000);
    return () => clearTimeout(id);
  }, [resendTimer]);

  async function handleVerify(e) {
    e.preventDefault();
    if (!otp.trim()) {
      setError('Please enter the OTP.');
      return;
    }
    setError('');
    setLoading(true);
    mlog('otp', 'verify_start');
    try {
      const res = await otpPost('/auth/verify-otp', { phone, otp: otp.trim() });
      const { access_token: accessToken, refresh_token: refreshToken } = res.data;
      const meRes = await authGet('/auth/me', {
        Authorization: `Bearer ${accessToken}`,
      });
      const userData = meRes.data;
      mlog('auth', 'login_success', { role: userData.role });
      login(accessToken, userData, refreshToken);
      navigateAfterLogin(userData, navigate);
    } catch (err) {
      mlogError('otp', 'verify_fail', err);
      setError(
        formatApiErrorDetail(err.response?.data?.detail) || 'Invalid OTP. Please try again.',
      );
    } finally {
      setLoading(false);
    }
  }

  async function handleResend() {
    if (resendTimer > 0) return;
    setResending(true);
    setError('');
    try {
      const res = await otpPost('/auth/send-otp', { phone });
      const next = stashDevOtp(res.data?.dev_otp);
      if (next) {
        setOtp(next);
        setShowDevAssist(true);
      }
      setResendTimer(RESEND_COOLDOWN);
    } catch (err) {
      setError(formatApiErrorDetail(err.response?.data?.detail) || 'Could not resend OTP.');
    } finally {
      setResending(false);
    }
  }

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="inline-block mb-4 font-extrabold text-3xl">
            <span className="text-indigo-600">Medi</span><span className="text-green-500">Route</span>
          </div>
        </div>

        <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
          <h2 className="text-xl font-bold text-gray-900 mb-1">Enter OTP</h2>
          <p className="text-sm text-gray-500 mb-6">
            OTP sent to <span className="font-medium text-gray-700">{phone}</span>
          </p>

          {showDevAssist && otp && (
            <p className="text-xs bg-yellow-50 border border-yellow-200 text-yellow-800 px-3 py-2 rounded-lg mb-4">
              <strong>DEV MODE:</strong> OTP auto-filled — {otp}
            </p>
          )}

          <form onSubmit={handleVerify} className="flex flex-col gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">OTP Code</label>
              <input
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                value={otp}
                onChange={(e) => setOtp(e.target.value)}
                placeholder="Enter 6-digit OTP"
                maxLength={6}
                className="w-full border border-gray-300 rounded-xl px-4 py-3 text-sm text-center tracking-widest text-lg font-bold focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition"
                autoFocus
              />
            </div>

            {error && (
              <p className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded-lg">{error}</p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white font-semibold py-3 rounded-xl transition-colors"
            >
              {loading ? 'Verifying…' : 'Verify OTP'}
            </button>
          </form>

          <div className="mt-4 text-center">
            {resendTimer > 0 ? (
              <p className="text-sm text-gray-400">
                Resend OTP in <span className="font-semibold text-gray-600">{resendTimer}s</span>
              </p>
            ) : (
              <button
                onClick={handleResend}
                disabled={resending}
                className="text-sm text-indigo-600 hover:underline disabled:opacity-50"
              >
                {resending ? 'Resending…' : 'Resend OTP'}
              </button>
            )}
          </div>
        </div>

        <button
          onClick={() => navigate('/login')}
          className="mt-4 w-full text-sm text-gray-500 hover:text-gray-700 text-center"
        >
          ← Change phone number
        </button>
      </div>
    </div>
  );
}
