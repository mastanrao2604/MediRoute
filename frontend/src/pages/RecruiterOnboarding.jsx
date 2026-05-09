import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api/axios';
import { useAuth } from '../context/AuthContext';

export default function RecruiterOnboarding() {
  const navigate = useNavigate();
  const { user, login, token } = useAuth();
  const [form, setForm] = useState({
    company_name: user?.company_name || '',
    official_email: user?.official_email || '',
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  function handleChange(e) {
    setForm((f) => ({ ...f, [e.target.name]: e.target.value }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!form.company_name.trim()) { setError('Company name is required.'); return; }
    if (!form.official_email.trim() || !form.official_email.includes('@')) {
      setError('A valid official email is required.');
      return;
    }
    setError('');
    setLoading(true);
    try {
      const res = await api.post('/recruiter/profile', form);
      // Refresh user in context so Navbar + Dashboard reflect new data
      const meRes = await api.get('/auth/me');
      login(token, meRes.data);
      navigate('/recruiter/dashboard');
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to save profile.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="inline-block mb-4 font-extrabold text-3xl">
            <span className="text-indigo-600">Medi</span><span className="text-green-500">Route</span>
          </div>
          <p className="text-gray-500 text-sm">Recruiter Setup</p>
        </div>

        <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
          <h2 className="text-xl font-bold text-gray-900 mb-1">Company Details</h2>
          <p className="text-sm text-gray-500 mb-6">
            Tell us about your organisation. Your profile will be reviewed before you can post jobs.
          </p>

          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">
                Company / Hospital Name <span className="text-red-500">*</span>
              </label>
              <input
                type="text"
                name="company_name"
                value={form.company_name}
                onChange={handleChange}
                placeholder="e.g. Apollo Hospitals"
                className="w-full border border-gray-300 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition"
                autoFocus
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">
                Official Email <span className="text-red-500">*</span>
              </label>
              <input
                type="email"
                name="official_email"
                value={form.official_email}
                onChange={handleChange}
                placeholder="e.g. hr@apollohospitals.com"
                className="w-full border border-gray-300 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition"
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
              {loading ? 'Saving…' : 'Submit for Verification'}
            </button>
          </form>
        </div>

        <p className="text-center text-xs text-gray-400 mt-6">
          Our team will verify your company details within 24 hours.
        </p>
      </div>
    </div>
  );
}
