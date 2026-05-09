import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api/axios';
import Navbar from '../components/Navbar';

const JOB_TYPE_OPTIONS = [
  { value: 'india', label: 'India Only', desc: 'Jobs within India', color: 'border-green-400 bg-green-50 text-green-700' },
  { value: 'abroad', label: 'Abroad Only', desc: 'International jobs', color: 'border-blue-400 bg-blue-50 text-blue-700' },
  { value: 'both', label: 'Both', desc: 'Open to any location', color: 'border-amber-400 bg-amber-50 text-amber-700' },
];

const PASSPORT_OPTIONS = [
  { value: 'yes', label: 'Yes' },
  { value: 'no', label: 'No' },
  { value: 'unknown', label: 'Not Sure' },
];

export default function Preferences() {
  const navigate = useNavigate();
  const [form, setForm] = useState({
    job_type: 'india',
    preferred_country: '',
    passport_status: 'unknown',
  });
  const [loading, setLoading] = useState(false);
  const [fetching, setFetching] = useState(true);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    api.get('/preferences/me')
      .then((res) => {
        const d = res.data;
        setForm({
          job_type: d.job_type || 'india',
          preferred_country: d.preferred_country || '',
          passport_status: d.passport_status || 'unknown',
        });
      })
      .catch(() => {})
      .finally(() => setFetching(false));
  }, []);

  async function handleSubmit(e) {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      await api.post('/preferences', form);
      setSuccess(true);
      setTimeout(() => navigate('/jobs'), 1200);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to save preferences.');
    } finally {
      setLoading(false);
    }
  }

  if (fetching) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Navbar />
        <div className="flex justify-center py-20">
          <div className="w-8 h-8 border-4 border-indigo-600 border-t-transparent rounded-full animate-spin" />
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <Navbar />
      <div className="max-w-lg mx-auto px-4 py-8">
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-gray-900">Job Preferences</h1>
          <p className="text-sm text-gray-500 mt-1">Tell us where you want to work</p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="bg-white rounded-2xl border border-gray-100 shadow-sm p-6 flex flex-col gap-6"
        >
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-3">Job Location Preference</label>
            <div className="grid grid-cols-3 gap-3">
              {JOB_TYPE_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setForm((f) => ({ ...f, job_type: opt.value }))}
                  className={`border-2 rounded-xl p-3 text-left transition-all ${
                    form.job_type === opt.value
                      ? opt.color + ' border-opacity-100'
                      : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
                  }`}
                >
                  <p className="text-sm font-semibold">{opt.label}</p>
                  <p className="text-xs mt-0.5 opacity-75">{opt.desc}</p>
                </button>
              ))}
            </div>
          </div>

          {(form.job_type === 'abroad' || form.job_type === 'both') && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">Preferred Country</label>
              <input
                type="text"
                value={form.preferred_country}
                onChange={(e) => setForm((f) => ({ ...f, preferred_country: e.target.value }))}
                placeholder="e.g. UAE, Germany, Australia"
                className="w-full border border-gray-300 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition"
              />
            </div>
          )}

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-3">Do you have a passport?</label>
            <div className="flex gap-3">
              {PASSPORT_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setForm((f) => ({ ...f, passport_status: opt.value }))}
                  className={`flex-1 border-2 rounded-xl py-2.5 text-sm font-medium transition-all ${
                    form.passport_status === opt.value
                      ? 'border-indigo-500 bg-indigo-50 text-indigo-700'
                      : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          {error && (
            <p className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded-lg">{error}</p>
          )}

          {success && (
            <p className="text-sm text-green-700 bg-green-50 px-3 py-2 rounded-lg">
              Preferences saved! Redirecting to jobs…
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white font-semibold py-3 rounded-xl transition-colors"
          >
            {loading ? 'Saving…' : 'Save & Find Jobs'}
          </button>
        </form>
      </div>
    </div>
  );
}
