import { useState, useEffect } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import api from '../api/axios';
import MainLayout from '../layouts/MainLayout';
import Spinner from '../components/Spinner';
import { useAuth } from '../context/AuthContext';

export default function RecruiterDashboard() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [jobs, setJobs] = useState([]);
  const [fetching, setFetching] = useState(false);  // start false — shell renders immediately
  const [error, setError] = useState('');

  const isVerified = user?.is_verified === true;

  useEffect(() => {
    // AuthContext already refreshes /auth/me in the background. No duplicate call here.
    api.get('/recruiter/jobs')
      .then((res) => setJobs(res.data))
      .catch(() => setError('Failed to load jobs.'))
      .finally(() => setFetching(false));
  }, []);

  if (fetching) {
    return (
      <MainLayout>
        <div className="flex justify-center py-20"><Spinner /></div>
      </MainLayout>
    );
  }

  // — removed: shell always renders; data loads inline below —

  return (
    <MainLayout>
      <div className="max-w-3xl mx-auto px-4 py-4">

        {/* Header — always at the top so title is immediately visible */}
        <div className="flex items-center justify-between mb-4">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Recruiter Dashboard</h1>
            {user?.company_name && (
              <p className="text-sm text-gray-500 mt-0.5 flex items-center gap-1">
                {user.company_name}
                {isVerified
                  ? <span className="text-green-600 font-semibold ml-1">✔ Verified</span>
                  : <span className="text-amber-500 font-medium ml-1">(Not Verified)</span>}
              </p>
            )}
          </div>
          <button
            onClick={() => navigate('/recruiter/post-job')}
            disabled={!isVerified}
            title={!isVerified ? 'Verification required to post jobs' : ''}
            className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed text-white font-semibold px-4 py-2 rounded-xl text-sm transition-colors"
          >
            + Post Job
          </button>
        </div>

        {/* Verification banner — below the title */}
        {!isVerified && (
          <div className="bg-amber-50 border border-amber-200 rounded-2xl p-4 mb-4 flex items-start gap-3">
            <span className="text-amber-500 text-lg">⏳</span>
            <div>
              <p className="text-sm font-semibold text-amber-800">Account under verification</p>
              <p className="text-xs text-amber-700 mt-0.5">
                Our team is reviewing your company details. You'll be able to post jobs once verified.
              </p>
              {!user?.company_name && (
                <button
                  onClick={() => navigate('/recruiter/onboarding')}
                  className="mt-2 text-xs text-indigo-600 font-semibold underline"
                >
                  Complete company profile →
                </button>
              )}
            </div>
          </div>
        )}

        {error && <p className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded-lg mb-4">{error}</p>}

        {jobs.length === 0 ? (
          <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-10 text-center">
            <p className="text-gray-500 mb-4">No jobs posted yet.</p>
            {isVerified && (
              <button
                onClick={() => navigate('/recruiter/post-job')}
                className="bg-indigo-600 hover:bg-indigo-700 text-white font-semibold px-6 py-3 rounded-xl text-sm transition-colors"
              >
                Post Your First Job
              </button>
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {jobs.map((job) => (
              <Link
                key={job.id}
                to={`/recruiter/jobs/${job.id}/applicants`}
                className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5 hover:border-indigo-200 transition-colors block"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="font-semibold text-gray-900 truncate">{job.title}</h3>
                    <p className="text-sm text-gray-500 mt-0.5 truncate">
                      {job.hospital_name || '—'} · {job.location || '—'}
                    </p>
                    {job.salary && <p className="text-sm text-green-600 mt-0.5">{job.salary}</p>}
                  </div>
                  <span className="shrink-0 text-xs bg-indigo-50 text-indigo-700 px-2 py-1 rounded-lg font-medium whitespace-nowrap">
                    View →
                  </span>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </MainLayout>
  );
}
