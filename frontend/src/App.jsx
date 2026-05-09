import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { GoogleOAuthProvider } from '@react-oauth/google';
import { AuthProvider } from './context/AuthContext';
import { useAuth } from './context/AuthContext';
import { lazy, Suspense } from 'react';
import ProtectedRoute from './components/ProtectedRoute';
import InstallPrompt from './components/InstallPrompt';
import UpdatePrompt from './components/UpdatePrompt';
import { getPostLoginRoute } from './utils/authNav';

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

export default function App() {
  return (
    <GoogleOAuthProvider clientId={GOOGLE_CLIENT_ID}>
    <AuthProvider>
      <BrowserRouter>
        <UpdatePrompt />
        <InstallPrompt />
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

          <Route path="/" element={<ProtectedRoute><RoleHome /></ProtectedRoute>} />
          <Route path="*" element={<ProtectedRoute><RoleHome /></ProtectedRoute>} />
        </Routes>
        </Suspense>
      </BrowserRouter>
    </AuthProvider>
    </GoogleOAuthProvider>
  );
}
