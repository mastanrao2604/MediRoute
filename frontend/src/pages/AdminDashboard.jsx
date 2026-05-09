import { useState, useEffect, useRef } from 'react';
import api from '../api/axios';
import MainLayout from '../layouts/MainLayout';

// Admin secret is NEVER bundled in the frontend build.
// It is entered once per browser session and stored in sessionStorage only
// (cleared automatically when the browser tab/window closes).
const SESSION_KEY = 'mediroute_admin_secret';

export default function AdminDashboard() {
  const [recruiters, setRecruiters] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [approving, setApproving] = useState(null);
  const [toast, setToast] = useState('');

  // Runtime admin secret — read from sessionStorage or prompt user
  const [adminSecret, setAdminSecret] = useState(() => sessionStorage.getItem(SESSION_KEY) || '');
  const [secretInput, setSecretInput] = useState('');
  const secretRef = useRef(null);

  // Auto-fetch once we have the secret
  useEffect(() => {
    if (adminSecret) fetchPending();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adminSecret]);

  function handleSecretSubmit(e) {
    e.preventDefault();
    const val = secretInput.trim();
    if (!val) return;
    sessionStorage.setItem(SESSION_KEY, val);
    setAdminSecret(val);
    setSecretInput('');
  }

  async function fetchPending() {
    setLoading(true);
    setError('');
    try {
      const res = await api.get('/admin/recruiters/pending', {
        headers: { 'X-Admin-Secret': adminSecret },
      });
      setRecruiters(res.data);
    } catch (err) {
      if (err.response?.status === 403) {
        // Bad secret — clear it so user can re-enter
        sessionStorage.removeItem(SESSION_KEY);
        setAdminSecret('');
        setError('Access denied. Admin secret is incorrect.');
      } else {
        setError('Failed to load recruiters.');
      }
    } finally {
      setLoading(false);
    }
  }

  async function handleApprove(userId) {
    setApproving(userId);
    try {
      await api.patch(`/admin/verify-recruiter/${userId}`, null, {
        headers: { 'X-Admin-Secret': adminSecret },
      });
      setRecruiters((prev) => prev.filter((r) => r.id !== userId));
      showToast('Recruiter approved.');
    } catch {
      showToast('Failed to approve. Try again.');
    } finally {
      setApproving(null);
    }
  }

  function showToast(msg) {
    setToast(msg);
    setTimeout(() => setToast(''), 3000);
  }

  return (
    <MainLayout>

      {toast && (
        <div className="fixed top-16 left-1/2 -translate-x-1/2 z-50 bg-gray-900 text-white text-sm px-4 py-2 rounded-xl shadow-lg">
          {toast}
        </div>
      )}

      {/* Secret gate — shown when no verified secret in sessionStorage */}
      {!adminSecret && (
        <div className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
          <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-8 w-full max-w-sm">
            <h2 className="text-lg font-bold text-gray-900 mb-1">Admin Access</h2>
            <p className="text-sm text-gray-500 mb-5">Enter your admin secret to continue. This is never stored in the app bundle.</p>
            <form onSubmit={handleSecretSubmit} className="flex flex-col gap-3">
              <input
                ref={secretRef}
                type="password"
                value={secretInput}
                onChange={(e) => setSecretInput(e.target.value)}
                placeholder="Admin secret"
                autoFocus
                className="border border-gray-300 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
              />
              {error && <p className="text-red-600 text-xs">{error}</p>}
              <button
                type="submit"
                className="bg-indigo-600 hover:bg-indigo-700 text-white font-semibold py-3 rounded-xl transition-colors text-sm"
              >
                Unlock
              </button>
            </form>
          </div>
        </div>
      )}

      {adminSecret && (
        <div className="max-w-4xl mx-auto px-4 py-8">
          <div className="flex items-center justify-between mb-6">
            <div>
              <h1 className="text-2xl font-bold text-gray-900">Admin — Pending Recruiters</h1>
              <p className="text-sm text-gray-500 mt-1">
                Approve recruiters to allow them to post jobs.
              </p>
            </div>
            <button
              onClick={fetchPending}
              className="text-sm text-indigo-600 hover:underline"
            >
              Refresh
            </button>
          </div>

          {loading && (
            <div className="flex justify-center py-16">
              <div className="w-8 h-8 border-4 border-indigo-600 border-t-transparent rounded-full animate-spin" />
            </div>
          )}

          {!loading && error && (
            <div className="bg-red-50 border border-red-200 rounded-xl p-4 text-red-700 text-sm">
              {error}
            </div>
          )}

          {!loading && !error && recruiters.length === 0 && (
            <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-10 text-center">
              <p className="text-gray-400">No pending recruiters. All caught up!</p>
            </div>
          )}

          {!loading && !error && recruiters.length > 0 && (
            <>
              {/* ── Mobile cards (< lg) ── */}
              <div className="lg:hidden flex flex-col gap-3">
                {recruiters.map((r) => (
                  <div key={r.id} className="bg-white rounded-2xl border border-gray-100 shadow-sm p-4">
                    <div className="flex items-start justify-between gap-3 mb-3">
                      <div className="min-w-0">
                        <p className="font-semibold text-gray-900 text-sm">{r.name || '—'}</p>
                        <p className="text-xs text-indigo-600 font-medium mt-0.5">{r.company_name || '—'}</p>
                      </div>
                      <button
                        onClick={() => handleApprove(r.id)}
                        disabled={approving === r.id}
                        className="shrink-0 bg-green-600 hover:bg-green-700 disabled:opacity-60 text-white text-xs font-semibold px-4 py-2 rounded-lg transition-colors"
                      >
                        {approving === r.id ? 'Approving…' : 'Approve'}
                      </button>
                    </div>
                    <div className="flex flex-col gap-1.5 text-xs text-gray-600">
                      <div className="flex items-center gap-2">
                        <span className="text-gray-400 w-12 shrink-0">Email</span>
                        <span className="break-all">{r.official_email || '—'}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-gray-400 w-12 shrink-0">Phone</span>
                        <span className="font-mono tracking-wide">{r.phone}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>

              {/* ── Desktop table (lg+) ── */}
              <div className="hidden lg:block bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 border-b border-gray-100">
                    <tr>
                      <th className="px-5 py-3 text-left font-medium text-gray-500">Name</th>
                      <th className="px-5 py-3 text-left font-medium text-gray-500">Company</th>
                      <th className="px-5 py-3 text-left font-medium text-gray-500">Email</th>
                      <th className="px-5 py-3 text-left font-medium text-gray-500">Phone</th>
                      <th className="px-5 py-3 text-left font-medium text-gray-500">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50">
                    {recruiters.map((r) => (
                      <tr key={r.id} className="hover:bg-gray-50">
                        <td className="px-5 py-4 font-medium text-gray-900">{r.name || '—'}</td>
                        <td className="px-5 py-4 text-gray-600">{r.company_name || '—'}</td>
                        <td className="px-5 py-4 text-gray-600">{r.official_email || '—'}</td>
                        <td className="px-5 py-4 text-gray-600 font-mono">{r.phone}</td>
                        <td className="px-5 py-4">
                          <button
                            onClick={() => handleApprove(r.id)}
                            disabled={approving === r.id}
                            className="bg-green-600 hover:bg-green-700 disabled:opacity-60 text-white text-xs font-semibold px-4 py-2 rounded-lg transition-colors"
                          >
                            {approving === r.id ? 'Approving…' : 'Approve'}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}
    </MainLayout>
  );
}
