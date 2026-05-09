import { Navigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

// Returns the "home" route for a given role
function homeFor(role) {
  return role === 'recruiter' ? '/recruiter/dashboard' : '/dashboard';
}

// allowedRole (optional): if set, only that role may access this route.
// Any other role is redirected to their own home page.
export default function ProtectedRoute({ children, allowedRole }) {
  const { token, user, loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="flex flex-col items-center gap-3">
          <div className="w-10 h-10 border-4 border-indigo-600 border-t-transparent rounded-full animate-spin" />
          <p className="text-gray-500 text-sm">Loading…</p>
        </div>
      </div>
    );
  }

  // Use localStorage as a synchronous fallback.
  // login() sets localStorage before calling setToken(), so even if React
  // hasn't flushed the state update yet when navigate() is called, the
  // token is already in localStorage and we can trust it.
  const effectiveToken = token || localStorage.getItem('mediroute_token');

  if (!effectiveToken) {
    return <Navigate to="/login" replace />;
  }

  if (allowedRole && user?.role !== allowedRole) {
    return <Navigate to={homeFor(user?.role)} replace />;
  }

  return children;
}
