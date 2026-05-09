import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

export default function Navbar() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  function handleLogout() {
    logout();
    navigate('/login');
  }

  return (
    <nav className="bg-white border-b border-gray-200 sticky top-0 z-50">
      <div className="max-w-5xl mx-auto px-4 h-14 flex items-center justify-between gap-2">
        <Link to={user?.role === 'recruiter' ? '/recruiter/dashboard' : '/dashboard'} className="font-bold text-xl tracking-tight shrink-0">
          <span className="text-indigo-600">Medi</span><span className="text-green-500">Route</span>
        </Link>

        <div className="flex items-center gap-1 sm:gap-4 overflow-x-auto no-scrollbar">
          {user?.role === 'recruiter' ? (
            <>
              <Link to="/recruiter/dashboard" className="text-sm text-gray-600 hover:text-indigo-600 transition-colors font-medium whitespace-nowrap px-2 py-2">
                Dashboard
              </Link>
              <Link to="/recruiter/post-job" className={`text-sm font-medium px-3 py-2 rounded-lg transition-colors whitespace-nowrap ${user?.is_verified ? 'text-white bg-indigo-600 hover:bg-indigo-700' : 'text-gray-400 bg-gray-100 cursor-not-allowed'}`}>
                Post Job
              </Link>
              <Link to="/recruiter/dashboard" className="text-sm text-gray-600 hover:text-indigo-600 transition-colors font-medium whitespace-nowrap px-2 py-2">
                My Jobs
              </Link>
              {user?.company_name && (
                <span className="hidden sm:inline-flex items-center gap-1 text-xs font-medium text-gray-500 whitespace-nowrap">
                  {user.company_name}
                  {user.is_verified
                    ? <span className="text-green-600">✔</span>
                    : <span className="text-amber-500" title="Pending verification">⏳</span>}
                </span>
              )}
            </>
          ) : (
            <>
              <Link to="/jobs" className="text-sm text-gray-600 hover:text-indigo-600 transition-colors font-medium whitespace-nowrap px-2 py-2">
                Jobs
              </Link>
              <Link to="/profile" className="text-sm text-gray-600 hover:text-indigo-600 transition-colors font-medium whitespace-nowrap px-2 py-2">
                Profile
              </Link>
              <Link to="/dashboard" className="text-sm text-gray-600 hover:text-indigo-600 transition-colors font-medium whitespace-nowrap px-2 py-2">
                Applications
              </Link>
            </>
          )}
          {user && (
            <span className="text-sm text-gray-400 hidden sm:block truncate max-w-[120px]">
              {user.phone}
            </span>
          )}
          <button
            onClick={handleLogout}
            className="text-sm bg-red-50 text-red-600 hover:bg-red-100 px-3 py-2 rounded-lg font-medium transition-colors whitespace-nowrap shrink-0"
          >
            Logout
          </button>
        </div>
      </div>
    </nav>
  );
}
