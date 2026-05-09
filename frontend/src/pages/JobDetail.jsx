import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import api from '../api/axios';
import MainLayout from '../layouts/MainLayout';
import Spinner from '../components/Spinner';

export default function JobDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [job, setJob] = useState(null);
  const [loading, setLoading] = useState(true);
  const [applying, setApplying] = useState(false);
  const [toast, setToast] = useState('');
  const [applied, setApplied] = useState(false);

  useEffect(() => {
    api.get(`/jobs/${id}`)
      .then((res) => setJob(res.data))
      .catch(() => setJob(null))
      .finally(() => setLoading(false));
  }, [id]);

  function showToast(msg) {
    setToast(msg);
    setTimeout(() => setToast(''), 3000);
  }

  async function handleApply() {
    setApplying(true);
    try {
      await api.post('/applications', { job_id: Number(id) });
      setApplied(true);
      showToast('Application submitted!');
    } catch (err) {
      showToast(err.response?.data?.detail || 'Could not apply.');
    } finally {
      setApplying(false);
    }
  }

  const jobTypeBadge = {
    india: 'bg-green-100 text-green-700',
    abroad: 'bg-blue-100 text-blue-700',
    both: 'bg-amber-100 text-amber-700',
  };

  if (loading) {
    return (
      <MainLayout>
        <div className="flex justify-center py-20"><Spinner /></div>
      </MainLayout>
    );
  }

  if (!job) {
    return (
      <MainLayout>
        <div className="max-w-2xl mx-auto px-4 py-16 text-center">
          <p className="text-gray-400 text-lg mb-4">Job not found.</p>
          <button
            onClick={() => navigate('/jobs')}
            className="text-indigo-600 hover:underline text-sm"
          >
            ← Back to Jobs
          </button>
        </div>
      </MainLayout>
    );
  }

  return (
    <MainLayout>

      {toast && (
        <div className="fixed top-16 left-1/2 -translate-x-1/2 z-50 bg-gray-900 text-white text-sm px-4 py-2 rounded-xl shadow-lg">
          {toast}
        </div>
      )}

      <div className="max-w-2xl mx-auto px-4 py-8">
        <button
          onClick={() => navigate('/jobs')}
          className="text-sm text-gray-500 hover:text-gray-700 mb-6 flex items-center gap-1"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
          Back to Jobs
        </button>

        <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-6">
          <div className="flex items-start justify-between gap-3 mb-4">
            <div>
              <h1 className="text-xl font-bold text-gray-900">{job.title}</h1>
              {job.hospital_name && (
                <p className="text-indigo-600 font-medium mt-1">{job.hospital_name}</p>
              )}
            </div>
            {job.job_type && (
              <span className={`text-xs px-2.5 py-1 rounded-full font-medium shrink-0 ${jobTypeBadge[job.job_type] || 'bg-gray-100 text-gray-600'}`}>
                {job.job_type.charAt(0).toUpperCase() + job.job_type.slice(1)}
              </span>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3 mb-6">
            {job.location && (
              <div className="bg-gray-50 rounded-xl p-3">
                <p className="text-xs text-gray-400 mb-0.5">Location</p>
                <p className="text-sm font-medium text-gray-800">{job.location}</p>
              </div>
            )}
            {job.country && (
              <div className="bg-gray-50 rounded-xl p-3">
                <p className="text-xs text-gray-400 mb-0.5">Country</p>
                <p className="text-sm font-medium text-gray-800">{job.country}</p>
              </div>
            )}
            {job.salary && (
              <div className="bg-green-50 rounded-xl p-3">
                <p className="text-xs text-gray-400 mb-0.5">Salary</p>
                <p className="text-sm font-semibold text-green-700">{job.salary}</p>
              </div>
            )}
            {job.role_required && (
              <div className="bg-indigo-50 rounded-xl p-3">
                <p className="text-xs text-gray-400 mb-0.5">Role</p>
                <p className="text-sm font-medium text-indigo-700">
                  {job.role_required.replace('_', ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
                </p>
              </div>
            )}
          </div>

          {job.description && (
            <div className="mb-6">
              <h3 className="text-sm font-semibold text-gray-700 mb-2">Job Description</h3>
              <p className="text-sm text-gray-600 leading-relaxed whitespace-pre-line">{job.description}</p>
            </div>
          )}

          <button
            onClick={handleApply}
            disabled={applying || applied}
            className={`w-full font-semibold py-3 rounded-xl transition-colors ${
              applied
                ? 'bg-green-500 text-white cursor-default'
                : 'bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white'
            }`}
          >
            {applied ? 'Applied ✓' : applying ? 'Applying…' : 'Apply for this Job'}
          </button>
        </div>
      </div>
    </MainLayout>
  );
}
