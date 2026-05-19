import { useState, useEffect } from 'react';
import api from '../api/axios';
import MainLayout from '../layouts/MainLayout';
import JobCard from '../components/JobCard';
import ShiftListingCard from '../components/ShiftListingCard';
import { useAuth } from '../context/AuthContext';
import { formatApiErrorDetail } from '../utils/apiErrorMessage';

const ROLES = ['', 'nurse', 'staff_nurse', 'icu_nurse', 'ot_nurse', 'emergency_nurse', 'home_care_nurse', 'doctor', 'lab_tech', 'pharmacist', 'driver', 'front_office'];
const JOB_TYPES = ['', 'india', 'abroad', 'both'];

export default function Jobs() {
  const { user } = useAuth();
  const isCandidate = user?.role !== 'recruiter';
  const [jobs, setJobs] = useState([]);
  const [shifts, setShifts] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [applying, setApplying] = useState(null);
  const [toast, setToast] = useState('');
  const [filters, setFilters] = useState({ role: '', location: '', job_type: '' });

  async function fetchJobs() {
    setLoading(true);
    setError('');
    const params = { limit: 100 };
    if (filters.role) params.role = filters.role;
    if (filters.location) params.location = filters.location;
    if (filters.job_type) params.job_type = filters.job_type;

    const shiftParams = {};
    if (filters.role) shiftParams.role = filters.role;

    // Fetch jobs independently of shifts — an older backend or shifts-only failure must not wipe job listings.
    try {
      const jobRes = await api.get('/jobs/', { params });
      const data = jobRes.data;
      setJobs(Array.isArray(data) ? data : (data?.items ?? data?.jobs ?? []));
    } catch (err) {
      const msg =
        formatApiErrorDetail(err.response?.data?.detail) ||
        err.message ||
        'Network error';
      setError(`Failed to load jobs — ${msg}. Tap Search to retry.`);
      console.error('fetchJobs (/jobs/) error:', err.response?.status, err.response?.data);
      setJobs([]);
    }

    try {
      if (user?.role !== 'recruiter') {
        const shiftRes = await api.get('/shifts/browse', { params: shiftParams });
        const rawShifts = shiftRes.data?.shifts;
        setShifts(Array.isArray(rawShifts) ? rawShifts : []);
      } else {
        setShifts([]);
      }
    } catch (err) {
      console.warn(
        'fetchJobs (/shifts/browse) skipped or failed:',
        err.response?.status,
        formatApiErrorDetail(err.response?.data?.detail) || err.message,
      );
      setShifts([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchJobs();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- filters applied when user taps Search; rerun when role is known for browse endpoint
  }, [user?.role]);

  async function handleApply(jobId) {
    setApplying(jobId);
    try {
      await api.post('/applications', { job_id: jobId });
      showToast('Applied successfully!');
    } catch (err) {
      showToast(err.response?.data?.detail || 'Could not apply.');
    } finally {
      setApplying(null);
    }
  }

  function showToast(msg) {
    setToast(msg);
    setTimeout(() => setToast(''), 3000);
  }

  function handleFilter(e) {
    setFilters((f) => ({ ...f, [e.target.name]: e.target.value }));
  }

  const hasJobs = jobs.length > 0;
  const hasShifts = shifts.length > 0;
  const empty = !loading && !error && !hasJobs && !hasShifts;

  return (
    <MainLayout>

      {toast && (
        <div className="fixed top-16 left-1/2 -translate-x-1/2 z-50 bg-gray-900 text-white text-sm px-4 py-2 rounded-xl shadow-lg">
          {toast}
        </div>
      )}

      <div className="max-w-5xl mx-auto px-4 py-6">
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-gray-900">Jobs & shifts</h1>
          <p className="text-sm text-gray-500 mt-1">Instant shifts and permanent job postings</p>
        </div>

        <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-4 mb-6 grid grid-cols-1 sm:grid-cols-3 gap-3">
          <select
            name="role"
            value={filters.role}
            onChange={handleFilter}
            className="border border-gray-200 rounded-xl px-3 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            <option value="">All Roles</option>
            {ROLES.filter(Boolean).map((r) => (
              <option key={r} value={r}>{r.replace('_', ' ').replace(/\b\w/g, (c) => c.toUpperCase())}</option>
            ))}
          </select>

          <input
            type="text"
            name="location"
            value={filters.location}
            onChange={handleFilter}
            placeholder="Filter jobs by location"
            className="border border-gray-200 rounded-xl px-3 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />

          <select
            name="job_type"
            value={filters.job_type}
            onChange={handleFilter}
            className="border border-gray-200 rounded-xl px-3 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            <option value="">All Types</option>
            {JOB_TYPES.filter(Boolean).map((t) => (
              <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>
            ))}
          </select>

          <button
            type="button"
            onClick={fetchJobs}
            className="col-span-1 sm:col-span-3 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold py-3 rounded-xl transition-colors"
          >
            Search
          </button>
        </div>

        {loading && (
          <div className="flex justify-center py-16">
            <div className="w-8 h-8 border-4 border-indigo-600 border-t-transparent rounded-full animate-spin" />
          </div>
        )}

        {!loading && error && (
          <p className="text-center text-red-600 py-8">{error}</p>
        )}

        {!loading && !error && empty && (
          <div className="text-center py-16">
            <p className="text-gray-400 text-base">No open shifts or jobs match your filters.</p>
            <p className="text-gray-400 text-sm mt-2">Try clearing filters or check back soon.</p>
          </div>
        )}

        {!loading && !error && hasShifts && isCandidate && (
          <>
            <h2 className="text-lg font-semibold text-gray-900 mb-3">Instant shifts</h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-8">
              {shifts.map((s) => (
                <ShiftListingCard key={`shift-${s.id}`} shift={s} />
              ))}
            </div>
          </>
        )}

        {!loading && !error && hasJobs && (
          <>
            <h2 className="text-lg font-semibold text-gray-900 mb-3">Job postings</h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {jobs.map((job) => (
                <JobCard
                  key={job.id}
                  job={job}
                  showApply={isCandidate}
                  applyLoading={applying === job.id}
                  onApply={handleApply}
                />
              ))}
            </div>
          </>
        )}
      </div>
    </MainLayout>
  );
}
