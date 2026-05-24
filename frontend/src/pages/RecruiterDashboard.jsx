import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useNavigate, Link, useLocation } from 'react-router-dom';
import api from '../api/axios';
import MainLayout from '../layouts/MainLayout';
import Spinner from '../components/Spinner';
import { useAuth } from '../context/AuthContext';
import { useDispatchEvents } from '../context/DispatchContext';
import ShiftDispatchLive from '../components/recruiter/ShiftDispatchLive';
import RecruiterShiftDetailSheet from '../components/recruiter/RecruiterShiftDetailSheet';
import AssignedNurseProfileSheet from '../components/recruiter/AssignedNurseProfileSheet';
import ShiftApplicantsPanel from '../components/recruiter/ShiftApplicantsPanel';
import { mlog, mlogError } from '../utils/mobileLogger';
import { formatApiErrorDetail } from '../utils/apiErrorMessage';
import {
  SHIFT_CARD_STATUS,
  SEARCH_PHASE_LABEL,
  isPastShiftStart,
} from '../utils/staffingStatusCopy';
import { formatShiftDateTime } from '../utils/shiftDateTime';
import { triggerDispatchReconcile } from '../utils/dispatchReconcile';

const STAFF_SHIFT_STATUS_PILL = {
  dispatching: 'bg-blue-50 text-blue-800 border border-blue-100',
  receiving: 'bg-green-50 text-green-800 border border-green-100',
  open: 'bg-slate-50 text-slate-700 border border-slate-100',
  filled: 'bg-emerald-50 text-emerald-900 border border-emerald-100',
  search_paused: 'bg-emerald-50 text-emerald-900 border border-emerald-100',
  expired: 'bg-amber-50 text-amber-900 border border-amber-100',
  cancelled: 'bg-gray-100 text-gray-600 border border-gray-200',
};

function effectiveShiftStatus(shift, live) {
  const db = shift?.status;
  const searchActive = shift?.search_active !== false && !shift?.search_closed;
  const confirmed = shift?.confirmed_count ?? 0;
  const applied = shift?.applied_count ?? 0;

  // DB terminal states always win — WS is UX overlay only
  if (db === 'cancelled') return 'cancelled';
  if (db === 'expired') return 'expired';
  if (db === 'filled' && confirmed > 0) return 'filled';

  if (live?.type === 'shift_cancelled' && db !== 'cancelled') return 'cancelled';
  if (live?.type === 'shift_expired' && db !== 'expired') return 'expired';
  if (!searchActive && confirmed > 0 && (db === 'filled' || live?.type === 'shift_filled')) {
    return 'filled';
  }
  if (!searchActive && applied > 0 && confirmed === 0) return 'receiving';
  if (db === 'dispatching' || live?.type === 'dispatch_started' || live?.type === 'dispatch_wave_update' || live?.type === 'nurse_accepted' || live?.type === 'nurse_applied') {
    return 'dispatching';
  }
  if (searchActive && (applied > 0 || confirmed > 0)) return 'receiving';
  if (db === 'open') {
    return isPastShiftStart(shift?.shift_start) ? 'expired' : 'open';
  }
  return db || 'open';
}

/** Card pill status — defined locally so lazy-loaded dashboard never depends on a missing shared export. */
function resolveShiftCardStatus(shift, live) {
  if (!shift) return 'open';
  const confirmed = shift.confirmed_count ?? 0;
  const applied = shift.applied_count ?? 0;
  const searchActive = shift.search_active !== false && !shift.search_closed;
  if (shift.search_closed && confirmed > 0) return 'search_paused';
  if (shift.search_closed && applied > 0 && confirmed === 0) return 'receiving';
  if (searchActive && (applied > 0 || confirmed > 0)) return 'receiving';
  if (shift.status === 'filled' && confirmed > 0 && !searchActive) return 'filled';
  if (shift.status === 'dispatching' || shift.status === 'open') return 'dispatching';
  if (live?.type === 'nurse_accepted' && searchActive) return 'receiving';
  if (live?.type === 'nurse_applied' && searchActive) return 'receiving';
  return shift.status || 'open';
}

function safeStatusLabel(cardStatus, effective) {
  const key = cardStatus || effective || 'open';
  if (SHIFT_CARD_STATUS[key]) return SHIFT_CARD_STATUS[key];
  if (typeof key === 'string' && key.length > 0) {
    return key.charAt(0).toUpperCase() + key.slice(1).replace(/_/g, ' ');
  }
  return 'Shift';
}

function safeShiftRow(shift) {
  if (!shift || shift.id == null) return null;
  return {
    ...shift,
    status: shift.status || 'open',
    hospital_name: shift.hospital_name || 'Hospital',
    role_required: shift.role_required || 'nurse',
    applicants: Array.isArray(shift.applicants) ? shift.applicants : [],
    confirmed_count: Number(shift.confirmed_count) || 0,
    applied_count: Number(shift.applied_count) || 0,
  };
}

function applicantCanConfirm(applicant, shift) {
  if (!applicant || !shift) return false;
  if ((shift.confirmed_count ?? 0) >= 1) return false;
  if (shift.search_active === false || shift.search_closed) return false;
  const stage = applicant.lifecycle_stage;
  if (stage === 'applied' || stage === 'under_review') return true;
  return applicant.status === 'applied';
}

function shiftOnsiteStatusLine(shift) {
  const apps = shift?.applicants || [];
  const completed = apps.find(
    (a) => a.lifecycle_stage === 'completed' || a.assignment_status === 'completed',
  );
  const onSite = apps.find(
    (a) => a.lifecycle_stage === 'checked_in' || a.assignment_status === 'checked_in',
  );
  const confirmed = apps.find(
    (a) => a.lifecycle_stage === 'recruiter_confirmed'
      || (a.status === 'confirmed' && !onSite && !completed),
  );
  if (completed) {
    const when = completed.check_out_at
      ? formatShiftDateTime(completed.check_out_at, { dateStyle: 'short', timeStyle: 'short' })
      : null;
    return when ? `${completed.name} · completed ${when}` : `${completed.name} · shift completed`;
  }
  if (onSite) {
    const when = onSite.check_in_at
      ? formatShiftDateTime(onSite.check_in_at, { dateStyle: 'short', timeStyle: 'short' })
      : null;
    return when ? `${onSite.name} · on shift since ${when}` : `${onSite.name} · checked in on site`;
  }
  if (confirmed) return `${confirmed.name} · confirmed — awaiting check-in`;
  return null;
}

function isStaffSearchLive(shift, live) {
  if (shift?.search_closed || shift?.search_active === false) return false;
  if (isPastShiftStart(shift?.shift_start)) return false;
  const st = effectiveShiftStatus(shift, live);
  return st === 'dispatching' || st === 'receiving' || st === 'open';
}

export default function RecruiterDashboard() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user } = useAuth();
  const { getRecentEvents, getDispatchStartTime, getShiftStatus, clearShift, reconcileFromShifts } = useDispatchEvents();
  const [jobs, setJobs] = useState([]);
  const [shifts, setShifts] = useState([]);
  const [fetching, setFetching] = useState(false);
  const [error, setError] = useState('');
  const [shiftsError, setShiftsError] = useState('');
  const [shiftBusyId, setShiftBusyId] = useState(null);
  const [jobBusyId, setJobBusyId] = useState(null);
  const [detailShiftId, setDetailShiftId] = useState(null);
  const [repostShiftId, setRepostShiftId] = useState(null);
  const [highlightShiftId, setHighlightShiftId] = useState(null);
  const [profileNurse, setProfileNurse] = useState(null);
  const [profileShiftLabel, setProfileShiftLabel] = useState('');
  const [profileShiftId, setProfileShiftId] = useState(null);
  const [confirmingNurseId, setConfirmingNurseId] = useState(null);
  const shiftsLoadRef = useRef(null);

  const loadShifts = useCallback(() => {
    if (shiftsLoadRef.current) {
      shiftsLoadRef.current.abort();
    }
    const controller = new AbortController();
    shiftsLoadRef.current = controller;
    return api.get('/shifts/', { signal: controller.signal, timeout: 20000 })
      .then((res) => {
        if (controller.signal.aborted) return;
        const raw = res.data?.shifts;
        const list = Array.isArray(raw) ? raw.map(safeShiftRow).filter(Boolean) : [];
        setShifts(list);
        reconcileFromShifts(list);
        setShiftsError('');
      })
      .catch((err) => {
        if (controller.signal.aborted || err?.code === 'ERR_CANCELED') return;
        mlogError('dispatch', 'recruiter_shifts_load_fail', err, {
          status: err?.response?.status ?? null,
        });
        setShiftsError('Could not load staffing shifts.');
      })
      .finally(() => {
        if (shiftsLoadRef.current === controller) {
          shiftsLoadRef.current = null;
        }
      });
  }, [reconcileFromShifts]);

  const isVerified = user?.is_verified === true;

  useEffect(() => {
    api.get('/recruiter/jobs')
      .then((res) => setJobs(res.data))
      .catch(() => setError('Failed to load jobs.'))
      .finally(() => setFetching(false));
    loadShifts();
    triggerDispatchReconcile('recruiter_dashboard_open').catch(() => {});
  }, [loadShifts]);

  useEffect(() => {
    const onRefresh = () => { loadShifts(); };
    window.addEventListener('mr-recruiter-shifts-refresh', onRefresh);
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        loadShifts();
        triggerDispatchReconcile('recruiter_dashboard_visible').catch(() => {});
      }
    };
    document.addEventListener('visibilitychange', onVisible);
    const poll = setInterval(loadShifts, 60000);
    return () => {
      window.removeEventListener('mr-recruiter-shifts-refresh', onRefresh);
      document.removeEventListener('visibilitychange', onVisible);
      clearInterval(poll);
    };
  }, [loadShifts]);

  useEffect(() => {
    const createdId = location.state?.shiftId;
    const createdShift = location.state?.shift;
    if (location.state?.shiftCreated && createdId) {
      setHighlightShiftId(createdId);
      mlog('dispatch', 'recruiter_shift_created_nav', { shift_id: createdId });
      if (createdShift?.id) {
        const row = safeShiftRow(createdShift);
        if (row) {
          setShifts((prev) => {
            const rest = prev.filter((s) => s.id !== row.id);
            return [row, ...rest];
          });
          setShiftsError('');
        }
      }
      navigate(location.pathname, { replace: true, state: {} });
    }
  }, [location.state, location.pathname, navigate]);

  const hasDispatching = useMemo(
    () => shifts.some((s) => isStaffSearchLive(s, getShiftStatus(s.id))),
    [shifts, getShiftStatus],
  );

  useEffect(() => {
    if (!hasDispatching) return undefined;
    const id = setInterval(() => loadShifts(), 12_000);
    return () => clearInterval(id);
  }, [hasDispatching, loadShifts]);

  async function cancelStaffingShift(shiftId) {
    if (!window.confirm('Cancel this shift? Nurses will stop receiving offers immediately.')) return;
    const reason = window.prompt(
      'Optional: reason for cancellation (shown to nurses who applied)',
    );
    setShiftBusyId(shiftId);
    try {
      const body = reason && reason.trim() ? { reason: reason.trim() } : undefined;
      await api.post(`/shifts/${shiftId}/cancel`, body);
      await loadShifts();
    } catch (e) {
      const d = e?.response?.data?.detail;
      setShiftsError(typeof d === 'string' ? d : 'Could not cancel shift.');
    } finally {
      setShiftBusyId(null);
    }
  }

  async function confirmStaff(shiftId, nurseUserId) {
    const shiftRow = shifts.find((s) => s.id === shiftId);
    if ((shiftRow?.confirmed_count ?? 0) >= 1) {
      setShiftsError('This shift already has confirmed staff. Pilot supports one nurse per shift.');
      return;
    }
    if (!window.confirm('Confirm this nurse for the shift? Applications will close (one nurse per shift during pilot).')) return;
    setConfirmingNurseId(nurseUserId);
    setShiftBusyId(shiftId);
    try {
      await api.post(`/shifts/${shiftId}/confirm-staff`, { nurse_user_id: nurseUserId });
      await loadShifts();
      setProfileNurse(null);
      setProfileShiftLabel('');
      setProfileShiftId(null);
    } catch (e) {
      const d = e?.response?.data?.detail;
      setShiftsError(typeof d === 'string' ? d : 'Could not confirm staff.');
    } finally {
      setConfirmingNurseId(null);
      setShiftBusyId(null);
    }
  }

  async function markNoShow(shiftId, nurseUserId) {
    if (!window.confirm('Mark this nurse as a no-show? The shift will reopen for staffing.')) return;
    setShiftBusyId(shiftId);
    try {
      await api.post(`/shifts/${shiftId}/mark-no-show`, { nurse_user_id: nurseUserId });
      await loadShifts();
      triggerDispatchReconcile('recruiter_no_show').catch(() => {});
    } catch (e) {
      const d = e?.response?.data?.detail;
      setShiftsError(typeof d === 'string' ? d : 'Could not mark no-show.');
    } finally {
      setShiftBusyId(null);
    }
  }

  async function stopStaffSearch(shiftId) {
    if (!window.confirm('Stop accepting new applications? Confirm one nurse to finalize staffing.')) return;
    setShiftBusyId(shiftId);
    try {
      await api.post(`/shifts/${shiftId}/stop-search`);
      await loadShifts();
    } catch (e) {
      const d = e?.response?.data?.detail;
      setShiftsError(typeof d === 'string' ? d : 'Could not stop staff search.');
    } finally {
      setShiftBusyId(null);
    }
  }

  async function redispatchStaffingShift(shiftId) {
    setShiftBusyId(shiftId);
    try {
      await api.post(`/shifts/${shiftId}/re-dispatch`);
      clearShift(shiftId);
      await loadShifts();
      setDetailShiftId(null);
      setRepostShiftId(null);
    } catch (e) {
      const msg = formatApiErrorDetail(e?.response?.data?.detail)
        || 'Could not start searching again.';
      setShiftsError(msg);
      throw e;
    } finally {
      setShiftBusyId(null);
    }
  }

  async function archiveStaffingShift(shiftId) {
    if (!window.confirm('Remove this shift from your list? You can still find it in audit logs if needed.')) return;
    setShiftBusyId(shiftId);
    setShiftsError('');
    try {
      await api.post(`/shifts/${shiftId}/archive`);
      setShifts((prev) => prev.filter((s) => s.id !== shiftId));
      setDetailShiftId(null);
      await loadShifts();
    } catch (e) {
      const msg = formatApiErrorDetail(e?.response?.data?.detail)
        || (e?.response?.status === 405
          ? 'Delete is not available on the server yet. Deploy the latest backend to Render.'
          : 'Could not remove shift.');
      setShiftsError(msg);
    } finally {
      setShiftBusyId(null);
    }
  }

  async function archiveJob(jobId) {
    if (!window.confirm('Remove this job from your list? Applicants and records are kept on the server.')) return;
    setJobBusyId(jobId);
    setError('');
    try {
      await api.post(`/recruiter/jobs/${jobId}/archive`);
      setJobs((prev) => prev.filter((j) => j.id !== jobId));
    } catch (e) {
      const msg = formatApiErrorDetail(e?.response?.data?.detail)
        || (e?.response?.status === 405
          ? 'Delete is not available on the server yet. Deploy the latest backend to Render.'
          : 'Could not remove job.');
      setError(msg);
    } finally {
      setJobBusyId(null);
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
    <>
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

        {hasDispatching && (
          <DispatchActivityPanel getRecentEvents={getRecentEvents} getDispatchStartTime={getDispatchStartTime} />
        )}

        {shifts.length > 0 && (
          <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-4 mb-4">
            <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
              Staffing shifts
            </h2>
            <div className="flex flex-col gap-3">
              {(shifts.map(safeShiftRow).filter(Boolean)).map((s) => {
                const live = getShiftStatus(s.id);
                const effective = effectiveShiftStatus(s, live);
                const canCancel = effective !== 'cancelled' && effective !== 'filled' && effective !== 'expired';
                const canRedispatch = effective === 'expired' || effective === 'cancelled'
                  || ((s.confirmed_count ?? 0) === 0 && s.status === 'open'
                    && (s.applicants || []).some(
                      (a) => a.lifecycle_stage === 'no_show' || a.assignment_status === 'no_show',
                    ));
                const canArchive =
                  s.status === 'expired' || s.status === 'cancelled' || s.status === 'filled'
                  || effective === 'expired' || effective === 'cancelled';
                const cardStatus = resolveShiftCardStatus(s, live) || effective;
                const pill = STAFF_SHIFT_STATUS_PILL[cardStatus] || STAFF_SHIFT_STATUS_PILL.open;
                const isLive = isStaffSearchLive(s, live);
                const canStopSearch = isLive && (s.confirmed_count ?? 0) < 1;
                const highlighted = highlightShiftId === s.id;
                const hasApplicants = (s.applicants?.length || 0) > 0;

                const statusShort = safeStatusLabel(cardStatus, effective);
                const onsiteLine = shiftOnsiteStatusLine(s);

                return (
                  <div
                    key={s.id}
                    className={`rounded-xl border px-3 py-3 flex flex-col gap-2 transition-shadow ${
                      highlighted ? 'border-indigo-300 ring-2 ring-indigo-200 bg-indigo-50/30' :
                      isLive ? 'border-indigo-200 bg-indigo-50/20' :
                      'border-gray-100 bg-gray-50/50'
                    }`}
                  >
                    <button
                      type="button"
                      className="text-left w-full min-w-0"
                      onClick={() => setDetailShiftId(s.id)}
                    >
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className={`text-xs font-semibold px-2 py-0.5 rounded-lg ${pill}`}>
                          {statusShort}
                        </span>
                        <span className="text-xs text-gray-400">#{s.id}</span>
                        <span className="text-xs text-indigo-600 ml-auto font-medium">View details →</span>
                      </div>
                      <p className="text-sm font-medium text-gray-900 mt-1 truncate">{s.hospital_name}</p>
                      <p className="text-xs text-gray-500">
                        {s.role_required} · {formatShiftDateTime(s.shift_start)}
                      </p>
                      {onsiteLine && (
                        <p className="text-xs text-emerald-800 font-medium mt-1">{onsiteLine}</p>
                      )}
                    </button>

                    {isLive && (
                      <ShiftDispatchLive
                        shift={s}
                        live={live}
                        dispatchStartTime={getDispatchStartTime(s.id)}
                      />
                    )}

                    {(hasApplicants || live?.type === 'nurse_accepted' || live?.type === 'nurse_applied') && (
                      <ShiftApplicantsPanel
                        shift={s}
                        confirmingNurseId={confirmingNurseId}
                        onConfirmStaff={(nurse) => confirmStaff(s.id, nurse.user_id)}
                        onMarkNoShow={(nurse) => markNoShow(s.id, nurse.user_id)}
                        shiftBusy={shiftBusyId === s.id}
                        onViewProfile={(nurse) => {
                          setProfileNurse(nurse);
                          setProfileShiftId(s.id);
                          setProfileShiftLabel(`${s.hospital_name} · ${formatShiftDateTime(s.shift_start)}`);
                        }}
                      />
                    )}

                    {live?.type === 'nurse_accepted' && isLive && (s.confirmed_count ?? 0) < 1 && (
                      <p className="text-xs text-green-800 font-medium">
                        {live.message || 'Review applications and confirm one nurse for this shift.'}
                      </p>
                    )}

                    {!isLive && effective === 'filled' && (
                      <p className="text-xs text-green-800 font-semibold">
                        {shiftOnsiteStatusLine(s) || 'Staff confirmed'}
                      </p>
                    )}

                    {effective === 'expired' && (
                      <p className="text-xs text-amber-800 font-medium">
                        {live?.message || 'Shift expired — no staff confirmed in time.'}
                      </p>
                    )}

                    {(canCancel || canRedispatch || canArchive || canStopSearch) && isVerified && (
                      <div className="flex gap-2 shrink-0 flex-wrap justify-end pt-1 border-t border-gray-100/80">
                        {canStopSearch && (
                          <button
                            type="button"
                            disabled={shiftBusyId === s.id}
                            onClick={(e) => { e.stopPropagation(); stopStaffSearch(s.id); }}
                            className="text-xs font-semibold px-3 py-2 rounded-xl bg-amber-50 text-amber-900 border border-amber-200 hover:bg-amber-100 disabled:opacity-50"
                          >
                            Stop searching
                          </button>
                        )}
                        {canCancel && (
                          <button
                            type="button"
                            disabled={shiftBusyId === s.id}
                            onClick={(e) => { e.stopPropagation(); cancelStaffingShift(s.id); }}
                            className="text-xs font-semibold px-3 py-2 rounded-xl bg-red-50 text-red-700 border border-red-100 hover:bg-red-100 disabled:opacity-50"
                          >
                            Cancel
                          </button>
                        )}
                        {canRedispatch && (
                          <button
                            type="button"
                            disabled={shiftBusyId === s.id}
                            onClick={(e) => {
                              e.stopPropagation();
                              setRepostShiftId(s.id);
                              setDetailShiftId(s.id);
                            }}
                            className="text-xs font-semibold px-3 py-2 rounded-xl bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50"
                          >
                            Post again
                          </button>
                        )}
                        {canArchive && (
                          <button
                            type="button"
                            disabled={shiftBusyId === s.id}
                            onClick={(e) => { e.stopPropagation(); archiveStaffingShift(s.id); }}
                            className="text-xs font-semibold px-3 py-2 rounded-xl bg-gray-100 text-gray-700 hover:bg-gray-200 disabled:opacity-50"
                          >
                            Delete
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
              <div
                key={job.id}
                className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden"
              >
                <Link
                  to={`/recruiter/jobs/${job.id}/applicants`}
                  state={{
                    returnTo: '/recruiter/dashboard',
                    jobTitle: job.title,
                    jobHospital: job.hospital_name,
                  }}
                  className="block p-5 hover:bg-gray-50/80 transition-colors"
                >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="font-semibold text-gray-900 truncate">{job.title}</h3>
                    <p className="text-sm text-gray-500 mt-0.5 truncate">
                      {job.hospital_name || '—'} · {job.location || '—'}
                    </p>
                    {job.salary && <p className="text-sm text-green-600 mt-0.5">{job.salary}</p>}
                      {job.status && job.status !== 'open' && (
                        <span className="inline-block mt-1 text-xs font-medium px-2 py-0.5 rounded-lg bg-slate-100 text-slate-600 capitalize">
                          {job.status}
                        </span>
                      )}
                  </div>
                  <span className="shrink-0 text-xs bg-indigo-50 text-indigo-700 px-2 py-1 rounded-lg font-medium whitespace-nowrap">
                    View →
                  </span>
                </div>
              </Link>
                {isVerified && (
                  <div className="px-4 pb-3 pt-0 flex justify-end border-t border-gray-50">
                    <button
                      type="button"
                      disabled={jobBusyId === job.id}
                      onClick={() => archiveJob(job.id)}
                      className="text-xs font-semibold px-3 py-2 rounded-xl bg-gray-100 text-gray-700 hover:bg-gray-200 disabled:opacity-50"
                    >
                      Delete
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </MainLayout>

      {detailShiftId && (
        <RecruiterShiftDetailSheet
          shiftId={detailShiftId}
          repostIntent={repostShiftId === detailShiftId}
          busy={!!shiftBusyId}
          onClose={() => {
            setDetailShiftId(null);
            setRepostShiftId(null);
          }}
          onUpdated={async () => { await loadShifts(); }}
          onCancel={async (id) => { await cancelStaffingShift(id); setDetailShiftId(null); setRepostShiftId(null); }}
          onArchive={archiveStaffingShift}
          onRedispatch={redispatchStaffingShift}
          onStopSearch={stopStaffSearch}
          onConfirmStaff={confirmStaff}
          confirmingNurseId={confirmingNurseId}
          onViewNurseProfile={(nurse, shiftRow) => {
            setProfileNurse(nurse);
            setProfileShiftId(shiftRow.id);
            setProfileShiftLabel(
              `${shiftRow.hospital_name} · ${formatShiftDateTime(shiftRow.shift_start)}`,
            );
          }}
        />
      )}

      {profileNurse && (
        <AssignedNurseProfileSheet
          nurse={profileNurse}
          shiftLabel={profileShiftLabel}
          canConfirm={
            profileShiftId != null
            && applicantCanConfirm(
              profileNurse,
              shifts.find((s) => s.id === profileShiftId),
            )
          }
          confirmBusy={confirmingNurseId === profileNurse.user_id}
          onConfirmStaff={(nurse) => confirmStaff(profileShiftId, nurse.user_id)}
          onClose={() => {
            setProfileNurse(null);
            setProfileShiftLabel('');
            setProfileShiftId(null);
          }}
        />
      )}
    </>
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
    label: 'Still searching for staff…',
    dot: 'bg-indigo-500', text: 'text-indigo-700', bg: 'bg-indigo-50',
    active: true,
  },
  nurse_accepted: {
    label: 'Receiving applications',
    dot: 'bg-green-500', text: 'text-green-700', bg: 'bg-green-50',
    active: true,
  },
  nurse_applied: {
    label: 'New application',
    dot: 'bg-green-500', text: 'text-green-700', bg: 'bg-green-50',
    active: true,
  },
  shift_search_stopped: {
    label: 'Search paused',
    dot: 'bg-emerald-500', text: 'text-emerald-800', bg: 'bg-emerald-50',
    active: false,
  },
  shift_filled: {
    label: 'Staff confirmed',
    dot: 'bg-green-500', text: 'text-green-700', bg: 'bg-green-50',
    active: false,
  },
  shift_expired: {
    label: 'Shift expired',
    dot: 'bg-amber-400', text: 'text-amber-700', bg: 'bg-amber-50',
    active: false,
  },
  shift_cancelled: {
    label: 'Shift cancelled',
    dot: 'bg-gray-400', text: 'text-gray-700', bg: 'bg-gray-50',
    active: false,
  },
  nurse_checked_in: {
    label: 'Staff on site',
    dot: 'bg-green-500', text: 'text-green-700', bg: 'bg-green-50',
    active: false,
  },
  nurse_checked_out: {
    label: 'Shift completed',
    dot: 'bg-slate-500', text: 'text-slate-700', bg: 'bg-slate-50',
    active: false,
  },
  dispatch_error: {
    label: 'Search interrupted',
    dot: 'bg-red-500', text: 'text-red-700', bg: 'bg-red-50',
    active: false,
  },
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
    const t = setInterval(() => setTick(n => n + 1), 1_000);
    return () => clearInterval(t);
  }, []);

  const events = getRecentEvents(5);

  if (events.length === 0) {
    return (
      <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-4 mb-4">
        <div className="flex items-center gap-2 mb-2">
          <div className="w-2 h-2 rounded-full bg-gray-300" />
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
            Live staff search
          </h3>
        </div>
        <p className="text-xs text-gray-400">
          No active searches yet. Use <strong>⚡ Post Shift</strong> to find nurses for an urgent shift.
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
          Live staff search
        </h3>
        {hasActiveEvent && (
          <span className="ml-auto text-xs text-blue-600 font-medium animate-pulse">
            Finding staff…
          </span>
        )}
      </div>
      <div className="flex flex-col gap-2">
        {events.map((ev) => {
          const meta = EVENT_META[ev.type] || EVENT_META.dispatch_wave_update;

          // Pick the most informative label
          let label = meta.label;
          if (ev.type === 'dispatch_wave_update' && ev.status) {
            label = SEARCH_PHASE_LABEL[ev.status] || label;
          }

          let displayMsg = ev.message;
          if (!displayMsg && ev.type === 'shift_expired') {
            displayMsg = 'No nurse accepted before the shift start time. Use Post again to retry.';
          }
          if (!displayMsg && ev.type === 'nurse_accepted') {
            displayMsg = ev.message || 'A nurse accepted — search still active.';
          }
          if (!displayMsg && ev.type === 'shift_search_stopped') {
            displayMsg = ev.message || 'Staff search paused.';
          }
          if (!displayMsg && ev.type === 'shift_filled') {
            displayMsg = ev.message || 'Staff finalized for this shift.';
          }
          if (!displayMsg && ev.type === 'shift_cancelled') {
            displayMsg = 'This shift was cancelled — nurses will not receive further alerts.';
          }
          if (!displayMsg && ev.type === 'dispatch_started') {
            displayMsg = 'Looking for available nurses near your hospital…';
          }
          if (!displayMsg && ev.type === 'dispatch_error') {
            displayMsg = 'Staff search was interrupted — check the shift card below.';
          }

          const startTime = getDispatchStartTime(ev.shift_id);
          const elapsed = meta.active ? elapsedSince(startTime) : null;

          const nurseCount = ev.nurses_notified != null
            ? `${ev.nurses_notified} nurse${ev.nurses_notified === 1 ? '' : 's'} contacted`
            : null;

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
                  {nurseCount && (
                    <p className="text-xs text-gray-500 mt-1">{nurseCount}</p>
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
