import { useState, useEffect } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import api from '../api/axios';
import MainLayout from '../layouts/MainLayout';
import Spinner from '../components/Spinner';
import { useAuth } from '../context/AuthContext';
import { useDispatchEvents } from '../context/DispatchContext';

export default function RecruiterDashboard() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const { getRecentEvents } = useDispatchEvents();
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

        {/* Live Dispatch Activity — shows real-time events from the dispatch engine */}
        <DispatchActivityPanel getRecentEvents={getRecentEvents} />

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

// ── Dispatch Activity Panel ────────────────────────────────────────────────────
// Shows real-time dispatch events for the hospital, delivered via WebSocket.
// Events arrive in DispatchContext from the backend dispatch engine.

const EVENT_META = {
  dispatch_started:    { label: 'Searching',         dot: 'bg-blue-500',  text: 'text-blue-700',  bg: 'bg-blue-50'  },
  dispatch_wave_update:{ label: 'In Progress',        dot: 'bg-indigo-500',text: 'text-indigo-700',bg: 'bg-indigo-50'},
  shift_filled:        { label: 'Assigned',           dot: 'bg-green-500', text: 'text-green-700', bg: 'bg-green-50' },
  shift_expired:       { label: 'No Nurses Found',    dot: 'bg-red-400',   text: 'text-red-700',   bg: 'bg-red-50'   },
  dispatch_error:      { label: 'Error',              dot: 'bg-red-500',   text: 'text-red-700',   bg: 'bg-red-50'   },
};

function timeAgo(ts) {
  const sec = Math.floor((Date.now() - ts) / 1000);
  if (sec < 10)  return 'just now';
  if (sec < 60)  return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60)  return `${min}m ago`;
  return `${Math.floor(min / 60)}h ago`;
}

function DispatchActivityPanel({ getRecentEvents }) {
  // Re-render every 15s to keep "X ago" timestamps fresh
  const [, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick(n => n + 1), 15_000);
    return () => clearInterval(t);
  }, []);

  const events = getRecentEvents(5);
  if (events.length === 0) return null;

  return (
    <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-4 mb-4">
      <div className="flex items-center gap-2 mb-3">
        <div className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
          Live Dispatch Activity
        </h3>
      </div>
      <div className="flex flex-col gap-2">
        {events.map((ev) => {
          const meta = EVENT_META[ev.type] || EVENT_META.dispatch_wave_update;
          return (
            <div
              key={`${ev.shift_id}-${ev._ts}`}
              className={`flex items-start gap-2.5 rounded-xl px-3 py-2.5 ${meta.bg}`}
            >
              <div className={`mt-1 w-2 h-2 rounded-full shrink-0 ${meta.dot}`} />
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-2">
                  <span className={`text-xs font-semibold ${meta.text}`}>{meta.label}</span>
                  <span className="text-xs text-gray-400 shrink-0">{timeAgo(ev._ts)}</span>
                </div>
                {ev.message && (
                  <p className="text-xs text-gray-600 mt-0.5 leading-snug">{ev.message}</p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
