import { BrowserRouter, Routes, Route, Navigate, useNavigate } from 'react-router-dom';
import { GoogleOAuthProvider } from '@react-oauth/google';
import { AuthProvider } from './context/AuthContext';
import { useAuth } from './context/AuthContext';
import { lazy, Suspense, Component, useEffect, useState, useCallback } from 'react';
import * as Sentry from '@sentry/react';
import ProtectedRoute from './components/ProtectedRoute';
import InstallPrompt from './components/InstallPrompt';
import UpdatePrompt from './components/UpdatePrompt';
import { getPostLoginRoute } from './utils/authNav';
import { useWebSocket } from './hooks/useWebSocket';
import { usePushNotifications } from './hooks/usePushNotifications';
import DispatchOfferModal from './components/DispatchOfferModal';

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
    // Surface the error in the browser/logcat console for debugging.
    console.error('[MediRoute ErrorBoundary]', error, info?.componentStack);
    // Forward to Sentry (no-op if VITE_SENTRY_DSN is not set)
    Sentry.captureException(error, {
      contexts: { react: { componentStack: info?.componentStack } },
    });
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen bg-gray-50 flex flex-col items-center justify-center px-6 text-center">
          <div className="mb-4 text-4xl">⚠️</div>
          <h1 className="text-xl font-bold text-gray-800 mb-2">Something went wrong</h1>
          <p className="text-sm text-gray-500 mb-6">
            {this.state.error?.message || 'An unexpected error occurred.'}
          </p>
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
  const { user, token } = useAuth();
  const [currentOffer, setCurrentOffer] = useState(null);

  const handleMessage = useCallback((msg) => {
    switch (msg.type) {
      case 'dispatch_offer':
        // Task 16: Deduplicate same offer_id (double WS delivery or pending recovery)
        setCurrentOffer(prev => {
          if (prev?.offer_id === msg.offer_id) return prev;
          return DISPATCH_ELIGIBLE_ROLES.has(user?.role) ? msg : prev;
        });
        break;

      case 'assignment_confirmed':
        // Dismiss modal (we already handled this in modal itself, but belt-and-suspenders)
        setCurrentOffer(null);
        break;

      case 'shift_filled':
      case 'dispatch_started':
      case 'dispatch_wave_update':
      case 'shift_expired':
      case 'dispatch_error':
        // Hospital events — recruiter dashboard pages listen for these via the
        // useDispatchStatus hook (future). For now logged for debugging.
        console.debug('[dispatch]', msg.type, msg);
        break;

      default:
        break;
    }
  }, [user?.role]);

  // Only connect if authenticated (nurse eligible roles OR recruiter for hospital updates)
  const shouldConnect = !!token && !!user;
  useWebSocket(shouldConnect ? user : null, token, handleMessage);

  // FCM token registration + notification tap routing (Capacitor Android only).
  // onDispatchOffer reuses handleMessage so deduplication logic is shared.
  usePushNotifications(shouldConnect ? user : null, token, handleMessage);

  if (!currentOffer) return null;

  return (
    <DispatchOfferModal
      offer={currentOffer}
      onClose={() => setCurrentOffer(null)}
    />
  );
}


export default function App() {
  return (
    <GoogleOAuthProvider clientId={GOOGLE_CLIENT_ID}>
    <AuthProvider>
      <ErrorBoundary>
      <BrowserRouter>
        <AppLinkHandler />
        <UpdatePrompt />
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
          <Route path="/recruiter/post-job" element={<ProtectedRoute allowedRole="recruiter"><PostJob /></ProtectedRoute>} />
          <Route path="/recruiter/jobs/:jobId/applicants" element={<ProtectedRoute allowedRole="recruiter"><Applicants /></ProtectedRoute>} />
          <Route path="/recruiter/applications/:applicationId" element={<ProtectedRoute allowedRole="recruiter"><CandidateDetail /></ProtectedRoute>} />

          {/* Admin — only accessible to the VITE_ADMIN_PHONE user */}
          <Route path="/admin" element={<AdminRoute><AdminDashboard /></AdminRoute>} />
          <Route path="/admin/ops" element={<AdminRoute><DispatchOps /></AdminRoute>} />

          <Route path="/" element={<ProtectedRoute><RoleHome /></ProtectedRoute>} />
          <Route path="*" element={<ProtectedRoute><RoleHome /></ProtectedRoute>} />
        </Routes>
        </Suspense>
      </BrowserRouter>
      </ErrorBoundary>
    </AuthProvider>
    </GoogleOAuthProvider>
  );
}
