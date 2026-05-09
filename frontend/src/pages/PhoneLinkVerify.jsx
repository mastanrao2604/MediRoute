import { useState, useEffect } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import api from '../api/axios';
import { useAuth } from '../context/AuthContext';
import { navigateAfterLogin } from '../utils/authNav';

/**
 * PhoneLinkVerify — shown after Google sign-in when the user needs to verify
 * their phone number before getting full access.
 *
 * Expects location.state = { googleSessionToken: string }
 *
 * Two-step flow:
 *   1. User enters phone → POST /auth/google/send-otp → OTP sent
 *   2. User enters OTP  → POST /auth/google/verify-phone → JWT issued → navigate home
 */
export default function PhoneLinkVerify() {
  const { state } = useLocation();
  const googleSessionToken = state?.googleSessionToken || '';
  const navigate = useNavigate();
  const { login } = useAuth();

  const [phone, setPhone] = useState('');
  const [otp, setOtp] = useState('');
  const [devOtp, setDevOtp] = useState('');
  const [step, setStep] = useState('phone'); // 'phone' | 'otp'
  const [loading, setLoading] = useState(false);
  const [resending, setResending] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!googleSessionToken) navigate('/login', { replace: true });
  }, []);

  async function handleSendOTP(e) {
    e.preventDefault();
    if (!phone.trim()) {
      setError('Please enter your phone number.');
      return;
    }
    setError('');
    setLoading(true);
    try {
      const res = await api.post('/auth/google/send-otp', {
        google_session_token: googleSessionToken,
        phone: phone.trim(),
      });
      if (res.data.dev_otp) {
        setOtp(res.data.dev_otp);
        setDevOtp(res.data.dev_otp);
      }
      setStep('otp');
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to send OTP. Try again.');
    } finally {
      setLoading(false);
    }
  }

  async function handleVerifyOTP(e) {
    e.preventDefault();
    if (!otp.trim()) {
      setError('Please enter the OTP.');
      return;
    }
    setError('');
    setLoading(true);
    try {
      const res = await api.post('/auth/google/verify-phone', {
        google_session_token: googleSessionToken,
        phone: phone.trim(),
        otp: otp.trim(),
      });
      const { access_token: accessToken, refresh_token: refreshToken } = res.data;
      const meRes = await api.get('/auth/me', {
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      const userData = meRes.data;
      login(accessToken, userData, refreshToken);
      navigateAfterLogin(userData, navigate);
    } catch (err) {
      setError(err.response?.data?.detail || 'Invalid OTP. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  async function handleResend() {
    setResending(true);
    setError('');
    try {
      const res = await api.post('/auth/google/send-otp', {
        google_session_token: googleSessionToken,
        phone: phone.trim(),
      });
      if (res.data.dev_otp) {
        setOtp(res.data.dev_otp);
        setDevOtp(res.data.dev_otp);
      }
    } catch {
      setError('Could not resend OTP.');
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
          {step === 'phone' ? (
            <>
              <h2 className="text-xl font-bold text-gray-900 mb-1">Verify Your Phone</h2>
              <p className="text-sm text-gray-500 mb-6">
                Your Google account is verified. Enter your phone number to complete sign-in.
              </p>

              <form onSubmit={handleSendOTP} className="flex flex-col gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">
                    Phone Number
                  </label>
                  <input
                    type="tel"
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                    placeholder="+91 9876543210"
                    className="w-full border border-gray-300 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition"
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
                  {loading ? 'Sending…' : 'Send OTP'}
                </button>
              </form>
            </>
          ) : (
            <>
              <h2 className="text-xl font-bold text-gray-900 mb-1">Enter OTP</h2>
              <p className="text-sm text-gray-500 mb-6">
                OTP sent to <span className="font-medium text-gray-700">{phone}</span>
              </p>

              {devOtp && (
                <p className="text-xs bg-yellow-50 border border-yellow-200 text-yellow-800 px-3 py-2 rounded-lg mb-4">
                  <strong>DEV MODE:</strong> OTP auto-filled — {devOtp}
                </p>
              )}

              <form onSubmit={handleVerifyOTP} className="flex flex-col gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">OTP Code</label>
                  <input
                    type="text"
                    inputMode="numeric"
                    value={otp}
                    onChange={(e) => setOtp(e.target.value)}
                    placeholder="123456"
                    maxLength={6}
                    className="w-full border border-gray-300 rounded-xl px-4 py-3 text-sm tracking-widest text-center text-lg font-semibold focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition"
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
                  {loading ? 'Verifying…' : 'Verify & Continue'}
                </button>
              </form>

              <button
                onClick={handleResend}
                disabled={resending}
                className="w-full mt-3 text-sm text-indigo-600 hover:underline disabled:opacity-60"
              >
                {resending ? 'Resending…' : 'Resend OTP'}
              </button>

              <button
                onClick={() => { setStep('phone'); setOtp(''); setDevOtp(''); setError(''); }}
                className="w-full mt-1 text-sm text-gray-400 hover:text-gray-600"
              >
                Change phone number
              </button>
            </>
          )}
        </div>

        <p className="text-center text-xs text-gray-400 mt-6">
          By continuing, you agree to our Terms of Service
        </p>
      </div>
    </div>
  );
}
