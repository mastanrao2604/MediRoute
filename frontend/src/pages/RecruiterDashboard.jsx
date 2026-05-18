import { useState, useEffect, useCallback } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import api from '../api/axios';
import MainLayout from '../layouts/MainLayout';
import Spinner from '../components/Spinner';
import { useAuth } from '../context/AuthContext';
import { useDispatchEvents } from '../context/DispatchContext';

function formatShiftWhen(iso) {
  try {
    return new Date(iso).toLocaleString(undefined, {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso ?? '—';
  }
}

const STAFF_SHIFT_STATUS_PILL = {
  dispatching: 'bg-blue-50 text-blue-800 border border-blue-100',
  open: 'bg-slate-50 text-slate-700 border border-slate-100',
  filled: 'bg-green-50 text-green-800 border border-green-100',
  expired: 'bg-amber-50 text-amber-900 border border-amber-100',
  cancelled: 'bg-gray-100 text-gray-600 border border-gray-200',
};

export default function RecruiterDashboard() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const { getRecentEvents, getDispatchStartTime, getShiftStatus } = useDispatchEvents();
  const [jobs, setJobs] = useState([]);
  const [shifts, setShifts] = useState([]);
  const [fetching, setFetching] = useState(false);  // start false — shell renders immediately
  const [error, setError] = useState('');
  const [shiftsError, setShiftsError] = useState('');
  const [shiftBusyId, setShiftBusyId] = useState(null);

  const loadShifts = useCallback(() => (
    api.get('/shifts/')
      .then((res) => {
        setShifts(res.data?.shifts ?? []);
        setShiftsError('');
      })
      .catch(() => setShiftsError('Could not load staffing shifts.'))
  ), []);

  const isVerified = user?.is_verified === true;

  useEffect(() => {
    api.get('/recruiter/jobs')
      .then((res) => setJobs(res.data))
      .catch(() => setError('Failed to load jobs.'))
      .finally(() => setFetching(false));
    loadShifts();
  }, [loadShifts]);

  async function cancelStaffingShift(shiftId) {
    if (!window.confirm('Cancel this shift? Nurses will stop receiving offers immediately.')) return;
    setShiftBusyId(shiftId);
    try {
      await api.post(`/shifts/${shiftId}/cancel`);
      await loadShifts();
    } catch (e) {
      const d = e?.response?.data?.detail;
      setShiftsError(typeof d === 'string' ? d : 'Could not cancel shift.');
    } finally {
      setShiftBusyId(null);
    }
  }

  async function redispatchStaffingShift(shiftId) {
    setShiftBusyId(shiftId);
    try {
      await api.post(`/shifts/${shiftId}/re-dispatch`);
      await loadShifts();
    } catch (e) {
      const d = e?.response?.data?.detail;
      setShiftsError(typeof d === 'string' ? d : 'Could not restart dispatch.');
    } finally {
      setShiftBusyId(null);
    }
  }

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
          <div className="flex gap-2">
            <button
              onClick={() => navigate('/recruiter/post-shift')}
              disabled={!isVerified}
              title={!isVerified ? 'Verification required to post shifts' : 'Post an urgent real-time shift'}
              className="bg-green-600 hover:bg-green-700 disabled:opacity-40 disabled:cursor-not-allowed text-white font-semibold px-4 py-2 rounded-xl text-sm transition-colors"
            >
              ⚡ Post Shift
            </button>
            <button
              onClick={() => navigate('/recruiter/post-job')}
              disabled={!isVerified}
              title={!isVerified ? 'Verification required to post jobs' : ''}
              className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed text-white font-semibold px-4 py-2 rounded-xl text-sm transition-colors"
            >
              + Post Job
            </button>
          </div>
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
        {shiftsError && (
          <p className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded-lg mb-4">{shiftsError}</p>
        )}

        {shifts.length > 0 && (
          <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-4 mb-4">
            <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
              Staffing shifts
            </h2>
            <div className="flex flex-col gap-3">
              {shifts.map((s) => {
                const live = getShiftStatus(s.id);
                const effective = live?.type === 'shift_cancelled' ? 'cancelled' : s.status;
                const canCancel = effective !== 'cancelled' && effective !== 'filled';
                const canRedispatch = effective === 'expired' || effective === 'cancelled';
                const pill = STAFF_SHIFT_STATUS_PILL[effective] || STAFF_SHIFT_STATUS_PILL.open;

                const statusShort =
                  effective === 'dispatching' ? 'Searching' :
                    effective.charAt(0).toUpperCase() + effective.slice(1);

                return (
                  <div
                    key={s.id}
                    className="rounded-xl border border-gray-100 bg-gray-50/50 px-3 py-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between"
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className={`text-xs font-semibold px-2 py-0.5 rounded-lg ${pill}`}>
                          {statusShort}
                        </span>
                        <span className="text-xs text-gray-400">#{s.id}</span>
                      </div>
                      <p className="text-sm font-medium text-gray-900 mt-1 truncate">{s.hospital_name}</p>
                      <p className="text-xs text-gray-500">
                        {s.role_required} · {formatShiftWhen(s.shift_start)}
                      </p>
                      {live?.message && effective === 'dispatching' && (
                        <p className="text-xs text-indigo-700 mt-1 leading-snug">{live.message}</p>
                      )}
                    </div>
                    {(canCancel || canRedispatch) && isVerified && (
                      <div className="flex gap-2 shrink-0 flex-wrap justify-end">
                        {canCancel && (
                          <button
                            type="button"
                            disabled={shiftBusyId === s.id}
                            onClick={() => cancelStaffingShift(s.id)}
                            className="text-xs font-semibold px-3 py-2 rounded-xl bg-red-50 text-red-700 border border-red-100 hover:bg-red-100 disabled:opacity-50"
                          >
                            Cancel shift
                          </button>
                        )}
                        {canRedispatch && (
                          <button
                            type="button"
                            disabled={shiftBusyId === s.id}
                            onClick={() => redispatchStaffingShift(s.id)}
                            className="text-xs font-semibold px-3 py-2 rounded-xl bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50"
                          >
                            Re-post dispatch
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Live Dispatch Activity — shows real-time events from the dispatch engine */}
        <DispatchActivityPanel getRecentEvents={getRecentEvents} getDispatchStartTime={getDispatchStartTime} />

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

// active = still searching | terminal = final outcome
const EVENT_META = {
  dispatch_started: {
    label: 'Finding nearby nurses…',
    dot: 'bg-blue-500', text: 'text-blue-700', bg: 'bg-blue-50',
    active: true,
  },
  dispatch_wave_update: {
    label: 'Searching…',   // overridden per-status below
    dot: 'bg-indigo-500', text: 'text-indigo-700', bg: 'bg-indigo-50',
    active: true,
  },
  shift_filled: {
    label: 'Staff found ✓',
    dot: 'bg-green-500', text: 'text-green-700', bg: 'bg-green-50',
    active: false,
  },
  shift_expired: {
    label: 'No one accepted',
    dot: 'bg-amber-400', text: 'text-amber-700', bg: 'bg-amber-50',
    active: false,
  },
  shift_cancelled: {
    label: 'Shift cancelled',
    dot: 'bg-gray-400', text: 'text-gray-700', bg: 'bg-gray-50',
    active: false,
  },
  dispatch_error: {
    label: 'Dispatch error',
    dot: 'bg-red-500', text: 'text-red-700', bg: 'bg-red-50',
    active: false,
  },
};

// Per-status override for wave_update labels
const WAVE_STATUS_LABEL = {
  dispatching:   'Notifying available staff…',
  no_candidates: 'Expanding search area…',
  waiting:       'Waiting for responses…',
  timed_out:     'Wave timed out — expanding search…',
  watching:      'Watching for available staff…',
  watching_online: 'Staff coming online — notifying…',
};

function timeAgo(ts) {
  const sec = Math.floor((Date.now() - ts) / 1000);
  if (sec < 10)  return 'just now';
  if (sec < 60)  return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60)  return `${min}m ago`;
  return `${Math.floor(min / 60)}h ago`;
}

function elapsedSince(ts) {
  if (!ts) return null;
  const sec = Math.floor((Date.now() - ts) / 1000);
  if (sec < 60)  return `${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60)  return `${min}m ${sec % 60}s`;
  return `${Math.floor(min / 60)}h ${min % 60}m`;
}

function DispatchActivityPanel({ getRecentEvents, getDispatchStartTime }) {
  // Re-render every 5s to keep "X ago" timestamps and elapsed counter fresh
  const [, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick(n => n + 1), 5_000);
    return () => clearInterval(t);
  }, []);

  const events = getRecentEvents(5);

  if (events.length === 0) {
    return (
      <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-4 mb-4">
        <div className="flex items-center gap-2 mb-2">
          <div className="w-2 h-2 rounded-full bg-gray-300" />
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
            Live Dispatch Activity
          </h3>
        </div>
        <p className="text-xs text-gray-400">
          No active shifts yet. Use <strong>⚡ Post Shift</strong> to dispatch a real-time staffing request.
        </p>
      </div>
    );
  }

  const hasActiveEvent = events.some(ev => {
    const m = EVENT_META[ev.type];
    return m?.active;
  });

  return (
    <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-4 mb-4">
      <div className="flex items-center gap-2 mb-3">
        <div className={`w-2 h-2 rounded-full ${hasActiveEvent ? 'bg-blue-500 animate-pulse' : 'bg-gray-400'}`} />
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
          Live Dispatch Activity
        </h3>
        {hasActiveEvent && (
          <span className="ml-auto text-xs text-blue-600 font-medium animate-pulse">
            searching…
          </span>
        )}
      </div>
      <div className="flex flex-col gap-2">
        {events.map((ev) => {
          const meta = EVENT_META[ev.type] || EVENT_META.dispatch_wave_update;

          // Pick the most informative label
          let label = meta.label;
          if (ev.type === 'dispatch_wave_update' && ev.status) {
            label = WAVE_STATUS_LABEL[ev.status] || label;
          }

          // Friendly fallback message for terminal states missing a backend message
          let displayMsg = ev.message;
          if (!displayMsg && ev.type === 'shift_expired') {
            displayMsg = 'Shift window closed — no nurses accepted. Re-post to try again.';
          }
          if (!displayMsg && ev.type === 'shift_filled') {
            displayMsg = 'A nurse has been assigned to your shift.';
          }
          if (!displayMsg && ev.type === 'shift_cancelled') {
            displayMsg = 'Shift was cancelled — nurses will not receive further offers.';
          }
          if (!displayMsg && ev.type === 'dispatch_started') {
            displayMsg = 'Searching for nurses in your area…';
          }
          if (!displayMsg && ev.type === 'dispatch_error') {
            displayMsg = 'Dispatch was interrupted — check shift status below.';
          }

          // Elapsed time since dispatch started (only for active states)
          const startTime = getDispatchStartTime(ev.shift_id);
          const elapsed = meta.active ? elapsedSince(startTime) : null;

          // Show wave progress if available
          const waveInfo = ev.wave ? `Wave ${ev.wave}` : null;
          const nurseCount = ev.nurses_notified != null ? `${ev.nurses_notified} notified` : null;

          return (
            <div
              key={`${ev.shift_id}-${ev._ts}`}
              className={`rounded-xl px-3 py-2.5 ${meta.bg}`}
            >
              <div className="flex items-center gap-2.5">
                {/* Dot — pulses when active */}
                <div className="relative shrink-0 mt-0.5 w-2 h-2">
                  <div className={`w-2 h-2 rounded-full ${meta.dot}`} />
                  {meta.active && (
                    <div className={`absolute inset-0 rounded-full ${meta.dot} animate-ping opacity-60`} />
                  )}
                </div>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between gap-2">
                    <span className={`text-xs font-semibold ${meta.text}`}>{label}</span>
                    {elapsed ? (
                      <span className="text-xs text-blue-500 shrink-0 font-medium tabular-nums">
                        {elapsed}
                      </span>
                    ) : (
                      <span className="text-xs text-gray-400 shrink-0">{timeAgo(ev._ts)}</span>
                    )}
                  </div>

                  {/* Backend message — primary info */}
                  {displayMsg && (
                    <p className="text-xs text-gray-600 mt-0.5 leading-snug">{displayMsg}</p>
                  )}

                  {/* Inline spinner for active dispatching state */}
                  {meta.active && ev.status === 'dispatching' && (
                    <div className="flex items-center gap-1.5 mt-1.5">
                      <span className="w-3 h-3 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin inline-block shrink-0" />
                      <span className="text-xs text-indigo-600">Waiting for nurse response…</span>
                    </div>
                  )}

                  {/* Wave + nurse count pill row */}
                  {(waveInfo || nurseCount) && (
                    <div className="flex gap-1.5 mt-1.5 flex-wrap">
                      {waveInfo && (
                        <span className={`text-xs px-1.5 py-0.5 rounded font-medium bg-white/60 ${meta.text}`}>
                          {waveInfo}
                        </span>
                      )}
                      {nurseCount && (
                        <span className="text-xs px-1.5 py-0.5 rounded font-medium bg-white/60 text-gray-600">
                          {nurseCount}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
