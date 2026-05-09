import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

const ADMIN_PHONE = import.meta.env.VITE_ADMIN_PHONE || '';

// ── Nav items per role ────────────────────────────────────────────────────────
function getNavItems(user) {
  if (!user) return [];
  if (ADMIN_PHONE && user.phone === ADMIN_PHONE) {
    return [{ label: 'Admin Panel', path: '/admin', icon: 'shield' }];
  }
  if (user.role === 'recruiter') {
    return [
      { label: 'Dashboard', path: '/recruiter/dashboard', icon: 'home' },
      { label: 'Post Job',  path: '/recruiter/post-job',  icon: 'plus', disabled: !user.is_verified },
    ];
  }
  // All healthcare candidate roles
  return [
    { label: 'Dashboard', path: '/dashboard', icon: 'home' },
    { label: 'Jobs',      path: '/jobs',       icon: 'briefcase' },
    { label: 'Resume',    path: '/resume',     icon: 'document' },
    { label: 'Profile',   path: '/profile',    icon: 'user' },
  ];
}

// ── Inline SVG icons ──────────────────────────────────────────────────────────
function Icon({ name, className = 'w-5 h-5' }) {
  const paths = {
    home:      'M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6',
    briefcase: 'M21 13.255A23.931 23.931 0 0112 15c-3.183 0-6.22-.62-9-1.745M16 6V4a2 2 0 00-2-2h-4a2 2 0 00-2 2v2m4 6h.01M5 20h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z',
    document:  'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z',
    user:      'M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z',
    plus:      'M12 4v16m8-8H4',
    shield:    'M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z',
    logout:    'M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1',
  };
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={paths[name]} />
    </svg>
  );
}

// ── MainLayout ────────────────────────────────────────────────────────────────
export default function MainLayout({ children }) {
  const { user, logout } = useAuth();
  const { pathname } = useLocation();
  const navigate = useNavigate();

  const navItems = getNavItems(user);

  function isActive(path) {
    if (path === '/dashboard' || path === '/recruiter/dashboard' || path === '/admin') {
      return pathname === path;
    }
    return pathname === path || pathname.startsWith(path + '/');
  }

  function handleLogout() {
    logout();
    navigate('/login');
  }

  const roleLabel = user?.role
    ? user.role.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
    : '';

  return (
    <div className="min-h-screen bg-gray-50 lg:flex">

      {/* ── Desktop Sidebar ───────────────────────────────────────────────── */}
      <aside className="hidden lg:flex lg:flex-col lg:fixed lg:inset-y-0 lg:w-60 bg-white border-r border-gray-200 z-40">
        {/* Logo */}
        <div className="h-16 flex items-center px-5 border-b border-gray-100 shrink-0">
          <Link to={navItems[0]?.path || '/'} className="font-extrabold text-xl tracking-tight">
            <span className="text-indigo-600">Medi</span><span className="text-green-500">Route</span>
          </Link>
        </div>

        {/* Nav links */}
        <nav className="flex-1 px-3 py-4 overflow-y-auto space-y-1">
          {navItems.map((item) => {
            const active = isActive(item.path);
            return (
              <Link
                key={item.path}
                to={item.disabled ? '#' : item.path}
                onClick={item.disabled ? (e) => e.preventDefault() : undefined}
                className={`flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-colors ${
                  item.disabled
                    ? 'text-gray-300 cursor-not-allowed'
                    : active
                    ? 'bg-indigo-50 text-indigo-700'
                    : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                }`}
              >
                <Icon name={item.icon} className="w-5 h-5 shrink-0" />
                <span>{item.label}</span>
                {item.disabled && (
                  <span className="ml-auto text-[10px] text-amber-500 font-normal">Unverified</span>
                )}
              </Link>
            );
          })}
        </nav>

        {/* User info + logout */}
        <div className="border-t border-gray-100 p-4 shrink-0">
          {user && (
            <div className="mb-3 px-1">
              <p className="text-xs text-gray-500 font-medium truncate">{user.phone}</p>
              {roleLabel && (
                <span className="mt-1 inline-block text-xs bg-indigo-50 text-indigo-600 font-medium px-2 py-0.5 rounded-full">
                  {roleLabel}
                </span>
              )}
              {user.company_name && (
                <p className="text-xs text-gray-400 mt-1 truncate">{user.company_name}</p>
              )}
            </div>
          )}
          <button
            onClick={handleLogout}
            className="flex items-center gap-2 w-full px-3 py-2.5 rounded-xl text-sm font-medium text-red-600 hover:bg-red-50 transition-colors"
          >
            <Icon name="logout" className="w-4 h-4" />
            Log out
          </button>
        </div>
      </aside>

      {/* ── Main content area ─────────────────────────────────────────────── */}
      <div className="flex-1 lg:ml-60 flex flex-col min-h-screen">
        {/* Mobile top bar */}
        <header
          className="lg:hidden sticky top-0 z-50 bg-white border-b border-gray-200 shrink-0 flex flex-col"
          style={{ paddingTop: 'env(safe-area-inset-top)' }}
        >
          <div className="h-14 flex items-center justify-between px-4">
            <Link to={navItems[0]?.path || '/'} className="font-extrabold text-xl tracking-tight">
              <span className="text-indigo-600">Medi</span><span className="text-green-500">Route</span>
            </Link>
          <div className="flex items-center gap-3">
              {user?.company_name && (
                <span className="text-xs text-gray-400 truncate max-w-[90px] hidden sm:block">{user.company_name}</span>
              )}
              {user && (
                <span className="text-xs text-gray-400 hidden sm:block">{user.phone}</span>
              )}
              <button
                onClick={handleLogout}
                className="p-2 rounded-lg text-red-500 hover:bg-red-50 transition-colors"
                title="Log out"
              >
                <Icon name="logout" className="w-5 h-5" />
              </button>
            </div>
          </div>
        </header>

        {/* Page content — pb-24 reserves space for mobile bottom nav */}
        <main className="flex-1 pb-24 lg:pb-8">
          {children}
        </main>
      </div>

      {/* ── Mobile bottom nav ─────────────────────────────────────────────── */}
      <nav className="lg:hidden fixed bottom-0 inset-x-0 z-50 bg-white border-t border-gray-100">
        <div className="flex items-stretch h-16">
          {navItems.map((item) => {
            const active = isActive(item.path);
            return (
              <Link
                key={item.path}
                to={item.disabled ? '#' : item.path}
                onClick={item.disabled ? (e) => e.preventDefault() : undefined}
                className={`relative flex-1 flex flex-col items-center justify-center gap-0.5 pt-2 pb-1 text-[10px] font-medium transition-colors ${
                  item.disabled
                    ? 'text-gray-300 pointer-events-none'
                    : active
                    ? 'text-indigo-600'
                    : 'text-gray-400 hover:text-gray-700'
                }`}
              >
                {active && (
                  <span className="absolute top-0 left-1/2 -translate-x-1/2 w-8 h-0.5 bg-indigo-600 rounded-b-full" />
                )}
                <Icon name={item.icon} className="w-6 h-6 shrink-0" />
                <span className="leading-none">{item.label.split(' ')[0]}</span>
              </Link>
            );
          })}
        </div>
      </nav>
    </div>
  );
}
