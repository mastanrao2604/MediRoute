import { BrowserRouter, Routes, Route, Navigate, useNavigate } from 'react-router-dom';
import { GoogleOAuthProvider } from '@react-oauth/google';
import { AuthProvider } from './context/AuthContext';
import { useAuth } from './context/AuthContext';
import { AvailabilityProvider } from './context/AvailabilityContext';
import { DispatchProvider, useDispatchEvents } from './context/DispatchContext';
import { lazy, Suspense, Component, useEffect, useState, useCallback } from 'react';
import * as Sentry from '@sentry/react';
import ProtectedRoute from './components/ProtectedRoute';
import InstallPrompt from './components/InstallPrompt';
import UpdatePrompt from './components/UpdatePrompt';
import { getPostLoginRoute } from './utils/authNav';
import { useWebSocket } from './hooks/useWebSocket';
import { mlog } from './utils/mobileLogger';
import { Capacitor } from '@capacitor/core';
import { usePushNotifications } from './hooks/usePushNotifications';
import DispatchOfferModal from './components/DispatchOfferModal';
import LocationEducationModal from './components/LocationEducationModal';
import { isBeforeShiftStartUtc } from './utils/shiftDateTime';

// ── Global Error Boundary ─────────────────────────────────────────────────────
// Catches any render-time exception in the entire component tree.
// Without this, any React render error produces a permanent white screen.
class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }
  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }
  componentDidCatch(error, info) {
    const msg = error?.message || String(error);
    const stack = error?.stack || '';
    const compStack = info?.componentStack || '';
    // Verbose traces for adb logcat / Chrome remote debugging — filter tag "MediRoute" or "[MR EB]"
    console.error('[MR EB] Uncaught React render error:', error?.name, msg);
    console.error('[MR EB] error.stack:\n', stack);
    console.error('[MR EB] componentStack:', compStack);
    mlog('error', 'react_error_boundary', {
      name: error?.name || 'Error',
      msg: msg.slice(0, 500),
      stack_tail: stack.slice(-1500),
    });
    // Forward to Sentry (no-op if VITE_SENTRY_DSN is not set)
    Sentry.captureException(error, {
      contexts: { react: { componentStack: compStack } },
    });
  }
  render() {
    if (this.state.hasError) {
      const err = this.state.error;
      const showTech =
        import.meta.env.DEV ||
        (typeof window !== 'undefined' && window.localStorage?.getItem('mr_show_error_detail') === '1');
      return (
        <div className="min-h-screen bg-gray-50 flex flex-col items-center justify-center px-6 text-center">
          <div className="mb-4 text-4xl">⚠️</div>
          <h1 className="text-xl font-bold text-gray-800 mb-2">Something went wrong</h1>
          <p className="text-sm text-gray-500 mb-6">
            {err?.message || 'An unexpected error occurred.'}
          </p>
          {showTech && (
            <details className="text-left w-full max-w-xl mb-4 text-xs text-gray-600 bg-white border border-gray-200 rounded-xl p-3 overflow-auto max-h-48">
              <summary className="cursor-pointer font-semibold text-gray-800">Technical details (dev / mr_show_error_detail)</summary>
              <pre className="mt-2 whitespace-pre-wrap break-words">{err?.stack || String(err)}</pre>
            </details>
          )}
          <button
            onClick={() => { this.setState({ hasError: false, error: null }); window.location.href = '/'; }}
            className="bg-indigo-600 text-white text-sm font-semibold px-5 py-2.5 rounded-xl hover:bg-indigo-700 transition-colors"
          >
            Go to Home
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

// Redirects to the right home based on role
function RoleHome() {
  const { user } = useAuth();
  return <Navigate to={user?.role === 'recruiter' ? '/recruiter/dashboard' : '/dashboard'} replace />;
}

// ── Lazy-loaded pages (code splitting — each page only downloads when visited) ──
const Login               = lazy(() => import('./pages/Login'));
const OTPVerify           = lazy(() => import('./pages/OTPVerify'));
const Onboarding          = lazy(() => import('./pages/Onboarding'));
const Profile             = lazy(() => import('./pages/Profile'));
const Jobs                = lazy(() => import('./pages/Jobs'));
const JobDetail           = lazy(() => import('./pages/JobDetail'));
const Dashboard           = lazy(() => import('./pages/Dashboard'));
const ResumeBuilder       = lazy(() => import('./pages/ResumeBuilder'));
const RecruiterDashboard  = lazy(() => import('./pages/RecruiterDashboard'));
const PostJob             = lazy(() => import('./pages/PostJob'));
const Applicants          = lazy(() => import('./pages/Applicants'));
const CandidateDetail     = lazy(() => import('./pages/CandidateDetail'));
const RecruiterOnboarding = lazy(() => import('./pages/RecruiterOnboarding'));
const AdminDashboard      = lazy(() => import('./pages/AdminDashboard'));
const PhoneLinkVerify     = lazy(() => import('./pages/PhoneLinkVerify'));
const DispatchOps         = lazy(() => import('./pages/DispatchOps'));
const PostShift           = lazy(() => import('./pages/PostShift'));

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID || '';
const ADMIN_PHONE = import.meta.env.VITE_ADMIN_PHONE || '';

// Redirects already-authenticated users away from public pages (login, etc.)
// This is what makes "remember login" feel seamless — app restart → straight to dashboard
function PublicRoute({ children }) {
  const { token, user, loading } = useAuth();
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="w-10 h-10 border-4 border-indigo-600 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  // Use synchronous localStorage fallback — same pattern as ProtectedRoute.
  // login() writes localStorage BEFORE calling setToken/setUser, so by the
  // time navigate() fires and React re-renders this component, the token and
  // user are in localStorage even if React state hasn't flushed yet.
  const effectiveToken = token || localStorage.getItem('mediroute_token');
  let effectiveUser = user;
  if (!effectiveUser) {
    try {
      const stored = localStorage.getItem('mediroute_user');
      if (stored) effectiveUser = JSON.parse(stored);
    } catch { /* ignore */ }
  }

  if (effectiveToken && effectiveUser) {
    return <Navigate to={getPostLoginRoute(effectiveUser)} replace />;
  }
  return children;
}

// Admin-only route — redirects non-admin users to their home page
function AdminRoute({ children }) {
  const { user, token, loading } = useAuth();
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="w-10 h-10 border-4 border-indigo-600 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }
  if (!token) return <Navigate to="/login" replace />;
  if (user?.phone !== ADMIN_PHONE) {
    return <Navigate to={user?.role === 'recruiter' ? '/recruiter/dashboard' : '/dashboard'} replace />;
  }
  return children;
}

/**
 * Resolve a raw App Link URL to an in-app path.
 * Returns the destination path (e.g. "/jobs/35") or null if not a recognised link.
 */
function _resolveDeepLinkPath(rawUrl) {
  try {
    const url = new URL(rawUrl);
    const match = url.pathname.match(/^\/share\/job\/(\d+)$/);
    if (match) return `/jobs/${match[1]}`;
  } catch { /* ignore */ }
  return null;
}

/**
 * AppLinkHandler — handles Android App Links for both cold-start and warm resumption.
 *
 * COLD START (app not running when link is tapped):
 *   Android launches the app then App.getLaunchUrl() returns the triggering URL.
 *   We must read it immediately on mount before any navigation happens.
 *
 * WARM (app already running in foreground/background):
 *   Android fires the appUrlOpen event which we catch with addListener.
 *
 * AUTH-AWARE:
 *   If the user is not logged in when the deep link arrives, we save the
 *   intended path to sessionStorage so navigateAfterLogin() can restore it.
 *
 * Must live inside <BrowserRouter> so useNavigate is available.
 */
function AppLinkHandler() {
  const navigate = useNavigate();
  useEffect(() => {
    let listenerHandle;
    (async () => {
      try {
        const { App } = await import('@capacitor/app');

        // Shared handler used for both cold-start and warm events
        const handleUrl = (rawUrl) => {
          const destPath = _resolveDeepLinkPath(rawUrl);
          if (!destPath) return;
          const isLoggedIn = !!localStorage.getItem('mediroute_token');
          if (isLoggedIn) {
            navigate(destPath, { replace: false });
          } else {
            // Save so navigateAfterLogin() restores it post-auth
            sessionStorage.setItem('mediroute_deep_link', destPath);
            navigate('/login', { replace: true });
          }
        };

        // COLD START — app was launched by tapping the link
        const launchData = await App.getLaunchUrl();
        if (launchData?.url) {
          handleUrl(launchData.url);
        }

        // WARM — link tapped while app is already running
        listenerHandle = await App.addListener('appUrlOpen', (event) => {
          handleUrl(event.url);
        });
      } catch (e) {
        // Not running in Capacitor (web/PWA) — App plugin not available, skip
      }
    })();
    return () => {
      listenerHandle?.remove();
    };
  }, [navigate]);
  return null;
}


/**
 * DispatchManager — global dispatch WebSocket handler.
 *
 * Rendered once inside AuthProvider + BrowserRouter.
 * For dispatch-eligible nurses: connects WebSocket, shows DispatchOfferModal on offer.
 * For recruiters: connects WebSocket to receive shift status updates (wave progress, filled).
 * Does nothing for unauthenticated users.
 */
const DISPATCH_ELIGIBLE_ROLES = new Set([
  'nurse', 'staff_nurse', 'icu_nurse', 'ot_nurse', 'emergency_nurse',
  'home_care_nurse', 'doctor', 'lab_tech', 'pharmacist', 'driver', 'front_office',
]);

function DispatchManager() {
  const { user, token, revalidate } = useAuth();
  const [currentOffer, setCurrentOffer] = useState(null);
  // minimizedOffer: offer was dismissed (×) but NOT declined — banner shown so nurse can reopen
  const [minimizedOffer, setMinimizedOffer] = useState(null);
  const { publish } = useDispatchEvents();

  // WS reconnect banner: only shown after 4s of confirmed disconnection
  // (avoids flashing on normal app start before first connection)
  const [showReconnectBanner, setShowReconnectBanner] = useState(false);

  // Dismiss (×): close the sheet but keep offer accessible via mini-banner
  const handleModalClose = useCallback(() => {
    setMinimizedOffer(prev => currentOffer ?? prev);
    setCurrentOffer(null);
  }, [currentOffer]);

  // Reopen: tap mini-banner to restore the sheet
  const handleBannerTap = useCallback(() => {
    if (!minimizedOffer) return;
    setCurrentOffer(minimizedOffer);
    setMinimizedOffer(null);
  }, [minimizedOffer]);

  const handleMessage = useCallback((msg) => {
    const tracePayload = () => ({
      offer_id: msg.offer_id != null ? Number(msg.offer_id) : undefined,
      shift_id: msg.shift_id != null ? Number(msg.shift_id) : undefined,
    });
    switch (msg.type) {
      case 'dispatch_offer':
        mlog('dispatch', 'ws_offer', tracePayload());
        // Deduplicate same offer_id (double WS delivery or WS+FCM)
        // Attach receivedAt so remaining time can be calculated on reopen
        setMinimizedOffer(null); // new offer supersedes any minimized one
        setCurrentOffer(prev => {
          if (prev?.offer_id === msg.offer_id) return prev;
          return DISPATCH_ELIGIBLE_ROLES.has(user?.role)
            ? { ...msg, _receivedAt: Date.now() }
            : prev;
        });
        if (DISPATCH_ELIGIBLE_ROLES.has(user?.role)) {
          window.dispatchEvent(new CustomEvent('mr-jobs-shifts-refresh'));
        }
        break;

      case 'offer_revoked': {
        mlog('dispatch', 'ws_offer_revoked', { shift_id: Number(msg.shift_id) });
        const sid = Number(msg.shift_id);
        setCurrentOffer(prev =>
          (Number(prev?.shift_id) === sid ? null : prev),
        );
        setMinimizedOffer(prev =>
          (Number(prev?.shift_id) === sid ? null : prev),
        );
        if (DISPATCH_ELIGIBLE_ROLES.has(user?.role)) {
          window.dispatchEvent(new CustomEvent('mr-jobs-shifts-refresh'));
        }
        break;
      }

      case 'application_submitted':
        mlog('dispatch', 'ws_application_submitted', tracePayload());
        setCurrentOffer(null);
        setMinimizedOffer(null);
        if (DISPATCH_ELIGIBLE_ROLES.has(user?.role)) {
          window.dispatchEvent(new CustomEvent('mr-nurse-active-shift-refresh'));
          window.dispatchEvent(new CustomEvent('mr-jobs-shifts-refresh'));
        }
        break;

      case 'assignment_confirmed':
        mlog('dispatch', 'ws_assignment_confirmed', tracePayload());
        setCurrentOffer(null);
        setMinimizedOffer(null);
        if (DISPATCH_ELIGIBLE_ROLES.has(user?.role)) {
          window.dispatchEvent(new CustomEvent('mr-nurse-active-shift-refresh'));
          window.dispatchEvent(new CustomEvent('mr-jobs-shifts-refresh'));
        }
        break;

      case 'dispatch_started':
      case 'dispatch_wave_update':
      case 'nurse_applied':
      case 'nurse_accepted':
      case 'shift_search_stopped':
      case 'shift_filled':
      case 'shift_expired':
      case 'shift_cancelled':
      case 'dispatch_error':
        mlog('dispatch', `ws_${msg.type}`, tracePayload());
        // Publish to DispatchContext — RecruiterDashboard reads from there
        publish(msg);
        if (
          user?.role === 'recruiter'
          && (msg.type === 'shift_filled' || msg.type === 'nurse_accepted' || msg.type === 'nurse_applied' || msg.type === 'shift_search_stopped')
        ) {
          window.dispatchEvent(new CustomEvent('mr-recruiter-shifts-refresh'));
        }
        if (DISPATCH_ELIGIBLE_ROLES.has(user?.role)) {
          if (
            msg.type === 'shift_cancelled'
            || msg.type === 'shift_expired'
            || msg.type === 'shift_filled'
            || msg.type === 'offer_revoked'
          ) {
            window.dispatchEvent(new CustomEvent('mr-nurse-active-shift-refresh'));
            window.dispatchEvent(new CustomEvent('mr-jobs-shifts-refresh'));
            if (msg.shift_id != null) {
              window.dispatchEvent(
                new CustomEvent('mr-jobs-shift-removed', { detail: { shiftId: Number(msg.shift_id) } }),
              );
            }
          }
        }
        if (msg.type === 'shift_expired' || msg.type === 'shift_cancelled') {
          const sid = Number(msg.shift_id);
          setCurrentOffer((prev) => (Number(prev?.shift_id) === sid ? null : prev));
          setMinimizedOffer((prev) => (Number(prev?.shift_id) === sid ? null : prev));
        }
        break;

      default:
        break;
    }
  }, [user?.role, publish]);

  const shouldConnect = !!token && !!user;
  const { isConnected } = useWebSocket(shouldConnect ? user : null, token, handleMessage, revalidate);

  // FCM token registration + notification tap routing (Capacitor Android only).
  usePushNotifications(shouldConnect ? user : null, token, handleMessage);

  // Reconnect banner logic: show only after sustained disconnection
  useEffect(() => {
    if (!shouldConnect) { setShowReconnectBanner(false); return; }
    if (isConnected) { setShowReconnectBanner(false); return; }
    const t = setTimeout(() => setShowReconnectBanner(true), 4000);
    return () => clearTimeout(t);
  }, [isConnected, shouldConnect]);

  const miniOfferActive = minimizedOffer?.shift_start
    ? isBeforeShiftStartUtc(minimizedOffer.shift_start)
    : Boolean(minimizedOffer);

  useEffect(() => {
    if (!minimizedOffer || miniOfferActive) return;
    setMinimizedOffer(null);
  }, [minimizedOffer, miniOfferActive]);

  return (
    <>
      {/* Reconnect banner — subtle, non-blocking, auto-dismisses on reconnect */}
      {showReconnectBanner && !minimizedOffer && (
        <div
          className="fixed top-0 inset-x-0 z-40 flex items-center justify-center gap-2 bg-amber-500 text-white text-xs font-medium py-1.5 px-4"
          style={{ paddingTop: 'calc(env(safe-area-inset-top, 0px) + 0.375rem)' }}
        >
          <span className="w-2.5 h-2.5 border-2 border-white border-t-transparent rounded-full animate-spin shrink-0" />
          Reconnecting for live shift alerts…
        </div>
      )}

      {/* Minimized offer mini-banner — tapping reopens the dispatch sheet */}
      {!currentOffer && minimizedOffer && miniOfferActive && (
        <button
          onClick={handleBannerTap}
          className="fixed top-0 inset-x-0 z-40 flex items-center justify-center gap-2 bg-orange-500 text-white text-xs font-semibold py-1.5 px-4 w-full text-left"
          style={{ paddingTop: 'calc(env(safe-area-inset-top, 0px) + 0.375rem)' }}
        >
          <span className="w-2 h-2 rounded-full bg-white animate-pulse shrink-0" />
          Shift invitation waiting — tap to view
        </button>
      )}

      {currentOffer && (
        <DispatchOfferModal
          offer={currentOffer}
          onClose={handleModalClose}
        />
      )}
    </>
  );
}


export default function App() {
  return (
    <GoogleOAuthProvider clientId={GOOGLE_CLIENT_ID}>
    <AuthProvider>
    <DispatchProvider>
      <ErrorBoundary>
      <BrowserRouter>
        <AuthAwareShell />
      </BrowserRouter>
      </ErrorBoundary>
    </DispatchProvider>
    </AuthProvider>
    </GoogleOAuthProvider>
  );
}

/**
 * AuthAwareShell — reads user from AuthContext, wraps everything in AvailabilityProvider.
 * Must live inside AuthProvider + BrowserRouter so hooks work correctly.
 */
function AuthAwareShell() {
  const { user } = useAuth();

  // Log app cold-start once after React mounts (Capacitor is fully ready by then)
  useEffect(() => {
    mlog('lifecycle', 'app_start');
  }, []);

  // Log foreground / background transitions via Capacitor App plugin
  useEffect(() => {
    let handle;
    (async () => {
      try {
        const { App: CapApp } = await import('@capacitor/app');
        handle = await CapApp.addListener('appStateChange', ({ isActive }) => {
          mlog('lifecycle', isActive ? 'foreground' : 'background');
        });
      } catch { /* not running in Capacitor */ }
    })();
    return () => { handle?.remove?.(); };
  }, []);

  return (
    <AvailabilityProvider user={user}>
      <LocationEducationModal />
      <AppLinkHandler />
      {!Capacitor.isNativePlatform() && <UpdatePrompt />}
      <InstallPrompt />
      <DispatchManager />
      <Suspense fallback={
        <div className="min-h-screen flex items-center justify-center bg-gray-50">
          <div className="w-10 h-10 border-4 border-indigo-600 border-t-transparent rounded-full animate-spin" />
        </div>
      }>
      <Routes>
        <Route path="/login" element={<PublicRoute><Login /></PublicRoute>} />
        <Route path="/verify-otp" element={<OTPVerify />} />
        <Route path="/link-phone" element={<PhoneLinkVerify />} />

        <Route path="/onboarding" element={<ProtectedRoute><Onboarding /></ProtectedRoute>} />
        <Route path="/dashboard" element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />
        <Route path="/profile" element={<ProtectedRoute><Profile /></ProtectedRoute>} />
        <Route path="/jobs" element={<ProtectedRoute><Jobs /></ProtectedRoute>} />
        <Route path="/jobs/:id" element={<ProtectedRoute><JobDetail /></ProtectedRoute>} />
        <Route path="/resume" element={<ProtectedRoute><ResumeBuilder /></ProtectedRoute>} />
        <Route path="/resume-builder" element={<ProtectedRoute><ResumeBuilder /></ProtectedRoute>} />

        {/* Recruiter — only accessible to users with role=recruiter */}
        <Route path="/recruiter/onboarding" element={<ProtectedRoute allowedRole="recruiter"><RecruiterOnboarding /></ProtectedRoute>} />
        <Route path="/recruiter/dashboard" element={<ProtectedRoute allowedRole="recruiter"><RecruiterDashboard /></ProtectedRoute>} />
        <Route path="/recruiter/post-job"   element={<ProtectedRoute allowedRole="recruiter"><PostJob /></ProtectedRoute>} />
        <Route path="/recruiter/post-shift" element={<ProtectedRoute allowedRole="recruiter"><PostShift /></ProtectedRoute>} />
        <Route path="/recruiter/jobs/:jobId/applicants" element={<ProtectedRoute allowedRole="recruiter"><Applicants /></ProtectedRoute>} />
        <Route path="/recruiter/applications/:applicationId" element={<ProtectedRoute allowedRole="recruiter"><CandidateDetail /></ProtectedRoute>} />

        {/* Admin — only accessible to the VITE_ADMIN_PHONE user */}
        <Route path="/admin" element={<AdminRoute><AdminDashboard /></AdminRoute>} />
        <Route path="/admin/ops" element={<AdminRoute><DispatchOps /></AdminRoute>} />

        <Route path="/" element={<ProtectedRoute><RoleHome /></ProtectedRoute>} />
        <Route path="*" element={<ProtectedRoute><RoleHome /></ProtectedRoute>} />
      </Routes>
      </Suspense>
    </AvailabilityProvider>
  );
}
