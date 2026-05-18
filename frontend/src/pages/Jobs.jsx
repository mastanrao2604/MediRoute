import { useState, useEffect } from 'react';
import api from '../api/axios';
import MainLayout from '../layouts/MainLayout';
import JobCard from '../components/JobCard';
import { useAuth } from '../context/AuthContext';

const ROLES = ['', 'nurse', 'staff_nurse', 'icu_nurse', 'ot_nurse', 'emergency_nurse', 'home_care_nurse', 'doctor', 'lab_tech', 'pharmacist', 'driver', 'front_office'];
const JOB_TYPES = ['', 'india', 'abroad', 'both'];

export default function Jobs() {
  const { user } = useAuth();
  const isCandidate = user?.role !== 'recruiter';
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(false);  // start false — shell renders immediately
  const [error, setError] = useState('');
  const [applying, setApplying] = useState(null);
  const [toast, setToast] = useState('');
  const [filters, setFilters] = useState({ role: '', location: '', job_type: '' });

  useEffect(() => {
    fetchJobs();
  }, []);

  async function fetchJobs() {
    setLoading(true);
    setError('');
    try {
      const params = {};
      if (filters.role) params.role = filters.role;
      if (filters.location) params.location = filters.location;
      if (filters.job_type) params.job_type = filters.job_type;
      const res = await api.get('/jobs', { params });
      //setJobs(res.data);
      const data = res.data;
      setJobs(Array.isArray(data) ? data : (data?.items?? data?.jobs ??[]));
    } catch (err) {
      const detail = err.response?.data?.detail || err.message || 'Network error';
      setError(`Failed to load jobs — ${detail}. Tap 'Search Jobs' to retry.`);
      console.error('fetchJobs error:', err.response?.status, detail);
    } finally {
      setLoading(false);
    }
  }

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

  return (
    <MainLayout>

      {toast && (
        <div className="fixed top-16 left-1/2 -translate-x-1/2 z-50 bg-gray-900 text-white text-sm px-4 py-2 rounded-xl shadow-lg">
          {toast}
        </div>
      )}

      <div className="max-w-5xl mx-auto px-4 py-6">
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-gray-900">Job Listings</h1>
          <p className="text-sm text-gray-500 mt-1">Find your next healthcare opportunity</p>
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
            placeholder="Filter by location"
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
            onClick={fetchJobs}
            className="col-span-1 sm:col-span-3 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-semibold py-3 rounded-xl transition-colors"
          >
            Search Jobs
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

        {!loading && !error && jobs.length === 0 && (
          <div className="text-center py-16">
            <p className="text-gray-400 text-base">No jobs found. Try different filters.</p>
          </div>
        )}

        {!loading && !error && (
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
        )}
      </div>
    </MainLayout>
  );
}
