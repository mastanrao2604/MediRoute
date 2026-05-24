import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api/axios';
import MainLayout from '../layouts/MainLayout';
import Spinner from '../components/Spinner';
import AvailabilityToggle from '../components/AvailabilityToggle';
import EmployeeShiftDetailSheet from '../components/EmployeeShiftDetailSheet';
import { useAuth } from '../context/AuthContext';
import { mlogError } from '../utils/mobileLogger';
import { formatShiftTime } from '../utils/shiftDateTime';
import {
  pickActiveNurseShift,
  pickCancelledNurseShifts,
  activeShiftSummaryLine,
} from '../utils/nurseActiveShift';
import {
  URGENCY_LABEL,
  formatRoleLabel,
  humanizeStaffingError,
  cancelledShiftStatusLabel,
  isApplicationPending,
  isApplicationFinalized,
  nurseLifecycleLabel,
} from '../utils/staffingStatusCopy';
import { filterJobSeekerOffers, SHIFT_ACCEPT_NEARBY_ONLY_MSG } from '../utils/shiftVisibility';

const DISPATCH_ELIGIBLE_ROLES = new Set([
  'nurse', 'staff_nurse', 'icu_nurse', 'ot_nurse', 'emergency_nurse',
  'home_care_nurse', 'doctor', 'lab_tech', 'pharmacist', 'driver', 'front_office',
]);

function nurseShiftDismissible(shift) {
  const a = shift?.assignment;
  if (!a) return false;
  const stage = a.lifecycle_stage;
  const st = a.status;
  if (st === 'checked_in' || stage === 'checked_in') return false;
  if (stage === 'recruiter_confirmed') return false;
  if (st === 'confirmed' && stage !== 'applied' && stage !== 'under_review') return false;
  return true;
}

function ShiftDismissButton({ shift, onDismiss, busy }) {
  if (!nurseShiftDismissible(shift)) return null;
  return (
    <button
      type="button"
      disabled={busy}
      onClick={(e) => {
        e.stopPropagation();
        onDismiss(shift.id);
      }}
      className="mt-2 text-xs font-semibold text-gray-500 hover:text-gray-700 underline disabled:opacity-50"
    >
      Remove from dashboard
    </button>
  );
}

export default function Dashboard() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [applications, setApplications] = useState([]);
  const [profile, setProfile] = useState(null);
  const [preferences, setPreferences] = useState(null);
  const [loading, setLoading] = useState(false);  // start false — shell renders immediately

  // Pending dispatch offers — loaded on mount + after visibility toggle
  const [pendingOffers, setPendingOffers]   = useState([]);
  const [acceptingId,   setAcceptingId]     = useState(null);
  const [offerError,    setOfferError]      = useState('');
  const [activeShift, setActiveShift]       = useState(null);
  const [cancelledShifts, setCancelledShifts] = useState([]);
  const [completedShifts, setCompletedShifts] = useState([]);
  const [staffingNotice, setStaffingNotice] = useState('');
  const [assignedDetailId, setAssignedDetailId] = useState(null);
  const [dismissBusyId, setDismissBusyId] = useState(null);
  const [allNurseShifts, setAllNurseShifts] = useState([]);

  const isDispatchEligible = DISPATCH_ELIGIBLE_ROLES.has(user?.role);

  const fetchActiveShift = useCallback(async () => {
    if (!isDispatchEligible) return;
    try {
      const res = await api.get('/shifts/');
      const list = res.data?.shifts || [];
      const active = pickActiveNurseShift(list);
      setActiveShift(active);
      setCancelledShifts(pickCancelledNurseShifts(list));
      setCompletedShifts(
        list.filter(
          (s) => s.assignment?.status === 'completed' || s.assignment?.lifecycle_stage === 'completed',
        ).slice(0, 5),
      );
      setAllNurseShifts(list);
      setAssignedDetailId((id) => {
        if (id == null) return null;
        if (active?.id === id) return id;
        if (list.some((s) => s.id === id)) return id;
        return null;
      });
    } catch {
      /* non-critical */
    }
  }, [isDispatchEligible]);

  const fetchPendingOffers = useCallback(async () => {
    if (!isDispatchEligible) return;
    try {
      const res = await api.get('/dispatch/offers/pending');
      setPendingOffers(filterJobSeekerOffers(res.data?.offers || []));
    } catch { /* non-critical */ }
  }, [isDispatchEligible]);

  useEffect(() => {
    // Single /dashboard call replaces 3 separate requests (profile + preferences + applications).
    // The backend aggregates them in one DB round-trip per relation.
    api.get('/dashboard/?app_limit=10')
      .then((res) => {
        const data = res.data || {};
        setApplications(data.applications || []);
        setProfile(data.profile ?? null);
        setPreferences(data.preferences ?? null);
      })
      .catch(() => {})
      .finally(() => setLoading(false));

    // Load pending dispatch offers (missed WebSocket deliveries)
    fetchPendingOffers();
    fetchActiveShift();
  }, [fetchPendingOffers, fetchActiveShift]);

  useEffect(() => {
    if (!isDispatchEligible) return undefined;
    const refresh = () => {
      fetchActiveShift();
      fetchPendingOffers();
    };
    window.addEventListener('mr-nurse-active-shift-refresh', refresh);
    const onStaffingNotice = (e) => {
      const msg = e.detail?.message;
      if (msg) {
        setStaffingNotice(msg);
        setTimeout(() => setStaffingNotice(''), 8000);
      }
      refresh();
    };
    window.addEventListener('mr-staffing-notice', onStaffingNotice);
    const onVisible = () => {
      if (document.visibilityState === 'visible') refresh();
    };
    document.addEventListener('visibilitychange', onVisible);
    const interval = setInterval(refresh, 60000);
    const onShiftRemoved = (e) => {
      const sid = e.detail?.shiftId;
      if (sid == null) return;
      setAssignedDetailId((id) => (id === sid ? null : id));
      setActiveShift((s) => (s?.id === sid ? null : s));
      setPendingOffers((prev) => prev.filter(
        (o) => Number(o.shift_id ?? o.shiftId) !== Number(sid),
      ));
      refresh();
    };
    window.addEventListener('mr-jobs-shift-removed', onShiftRemoved);
    return () => {
      window.removeEventListener('mr-nurse-active-shift-refresh', refresh);
      window.removeEventListener('mr-staffing-notice', onStaffingNotice);
      window.removeEventListener('mr-jobs-shift-removed', onShiftRemoved);
      document.removeEventListener('visibilitychange', onVisible);
      clearInterval(interval);
    };
  }, [isDispatchEligible, fetchActiveShift, fetchPendingOffers]);

  async function handleAcceptOffer(offerId) {
    setAcceptingId(offerId);
    setOfferError('');
    try {
      await api.post(`/dispatch/offers/${offerId}/accept`);
      setPendingOffers(prev => prev.filter(o => o.offer_id !== offerId));
      window.dispatchEvent(new CustomEvent('mr-jobs-shifts-refresh'));
      await fetchActiveShift();
      setTimeout(fetchActiveShift, 1200);
    } catch (err) {
      setOfferError(
        humanizeStaffingError(
          typeof err.response?.data?.detail === 'string'
            ? err.response.data.detail
            : 'Could not accept this shift. Try again.',
        ),
      );
    } finally {
      setAcceptingId(null);
    }
  }

  async function handleDeclineOffer(offerId) {
    try {
      await api.post(`/dispatch/offers/${offerId}/decline`);
      setPendingOffers(prev => prev.filter(o => o.offer_id !== offerId));
    } catch { /* ignore */ }
  }

  async function handleDismissShift(shiftId) {
    if (!window.confirm('Remove this shift from your dashboard? Records are kept on the server.')) return;
    setDismissBusyId(shiftId);
    try {
      await api.post(`/shifts/${shiftId}/dismiss`);
      setActiveShift((s) => (s?.id === shiftId ? null : s));
      setCancelledShifts((prev) => prev.filter((s) => s.id !== shiftId));
      setCompletedShifts((prev) => prev.filter((s) => s.id !== shiftId));
      setAllNurseShifts((prev) => prev.filter((s) => s.id !== shiftId));
      setAssignedDetailId((id) => (id === shiftId ? null : id));
      await fetchActiveShift();
    } catch (err) {
      mlogError('dispatch', 'nurse_shift_dismiss_fail', err, { shift_id: shiftId });
    } finally {
      setDismissBusyId(null);
    }
  }

  function profileCompletionScore() {
    if (!profile) return 0;
    let score = 0;
    if (profile.experience_years !== null) score += 25;
    if (profile.education) score += 25;
    if (profile.skills) score += 25;
    if (profile.current_location) score += 25;
    return score;
  }

  const completion = profileCompletionScore();

  const pastShifts = allNurseShifts.filter((s) => {
    const shown = new Set([
      activeShift?.id,
      ...cancelledShifts.map((x) => x.id),
      ...completedShifts.map((x) => x.id),
    ].filter(Boolean));
    return !shown.has(s.id) && nurseShiftDismissible(s);
  });

  const statusColors = {
    applied: 'bg-blue-100 text-blue-700',
    shortlisted: 'bg-green-100 text-green-700',
    rejected: 'bg-red-100 text-red-700',
  };

  if (loading) {
    return (
      <MainLayout>
        <div className="flex justify-center py-20"><Spinner /></div>
      </MainLayout>
    );
  }

  // — removed: shell always renders; data loads inline below —

  return (
    <MainLayout>
      <div className="max-w-5xl mx-auto px-4 py-6">
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
          <p className="text-sm text-gray-500 mt-1">Welcome back!</p>
        </div>

        {/* Availability toggle — only for dispatch-eligible healthcare workers */}
        {staffingNotice && (
          <div className="mb-4 rounded-xl bg-amber-50 border border-amber-200 px-4 py-3 text-sm text-amber-900">
            {staffingNotice}
          </div>
        )}

        {DISPATCH_ELIGIBLE_ROLES.has(user?.role) && (
          <div className="mb-4">
            <AvailabilityToggle
              activeShift={activeShift}
              onOpenActiveShift={(shift) => setAssignedDetailId(shift.id)}
            />
          </div>
        )}

        {isDispatchEligible && activeShift && (
          <div
            className={`mb-4 rounded-xl border px-4 py-3 text-sm ${
              isApplicationPending(activeShift)
                ? 'bg-blue-50 border-blue-200 text-blue-900'
                : isApplicationFinalized(activeShift)
                  ? 'bg-green-50 border-green-200 text-green-900'
                  : 'bg-gray-50 border-gray-200 text-gray-800'
            }`}
          >
            <p className="font-semibold">{activeShift.hospital_name}</p>
            <p className="mt-0.5">{nurseLifecycleLabel(activeShift)}</p>
            {(activeShift.assignment?.status === 'confirmed'
              || activeShift.assignment?.lifecycle_stage === 'recruiter_confirmed') && (
              <button
                type="button"
                onClick={() => setAssignedDetailId(activeShift.id)}
                className="mt-2 text-xs font-semibold text-green-800 underline"
              >
                Check in when you arrive →
              </button>
            )}
            {(activeShift.assignment?.status === 'checked_in'
              || activeShift.assignment?.lifecycle_stage === 'checked_in') && (
              <button
                type="button"
                onClick={() => setAssignedDetailId(activeShift.id)}
                className="mt-2 text-xs font-semibold text-green-800 underline"
              >
                Check out when shift ends →
              </button>
            )}
            <ShiftDismissButton
              shift={activeShift}
              onDismiss={handleDismissShift}
              busy={dismissBusyId === activeShift.id}
            />
          </div>
        )}

        {isDispatchEligible && completedShifts.length > 0 && (
          <div className="mb-6">
            <h2 className="text-sm font-bold text-gray-900 uppercase tracking-wide mb-2">
              Completed shifts
            </h2>
            <div className="flex flex-col gap-2">
              {completedShifts.map((s) => (
                <div
                  key={`completed-${s.id}`}
                  className="rounded-xl border border-slate-200 bg-slate-50/80 px-4 py-3"
                >
                  <button
                    type="button"
                    onClick={() => setAssignedDetailId(s.id)}
                    className="text-left w-full hover:opacity-90"
                  >
                  <p className="text-sm font-semibold text-gray-900">
                    {s.hospital_name} · {formatRoleLabel(s.role_required)}
                  </p>
                  <p className="text-xs text-slate-700 mt-0.5">Completed</p>
                  <p className="text-xs text-gray-500 mt-0.5">{activeShiftSummaryLine(s)}</p>
                  </button>
                  <ShiftDismissButton
                    shift={s}
                    onDismiss={handleDismissShift}
                    busy={dismissBusyId === s.id}
                  />
                </div>
              ))}
            </div>
          </div>
        )}

        {isDispatchEligible && cancelledShifts.length > 0 && (
          <div className="mb-6">
            <h2 className="text-sm font-bold text-gray-900 uppercase tracking-wide mb-2">
              Cancelled shifts
            </h2>
            <div className="flex flex-col gap-2">
              {cancelledShifts.map((s) => (
                <div
                  key={`cancelled-${s.id}`}
                  className="rounded-xl border border-red-100 bg-red-50/60 px-4 py-3"
                >
                  <button
                    type="button"
                    onClick={() => setAssignedDetailId(s.id)}
                    className="text-left w-full hover:bg-red-50"
                  >
                    <p className="text-sm font-semibold text-gray-900">
                      {s.hospital_name} · {formatRoleLabel(s.role_required)}
                    </p>
                    <p className="text-xs text-red-800 mt-0.5">{cancelledShiftStatusLabel(s)}</p>
                    <p className="text-xs text-gray-500 mt-0.5">{activeShiftSummaryLine(s)}</p>
                  </button>
                  <ShiftDismissButton
                    shift={s}
                    onDismiss={handleDismissShift}
                    busy={dismissBusyId === s.id}
                  />
                </div>
              ))}
            </div>
          </div>
        )}

        {isDispatchEligible && pastShifts.length > 0 && (
          <div className="mb-6">
            <h2 className="text-sm font-bold text-gray-900 uppercase tracking-wide mb-2">
              Past shifts
            </h2>
            <div className="flex flex-col gap-2">
              {pastShifts.map((s) => (
                <div
                  key={`past-${s.id}`}
                  className="rounded-xl border border-gray-200 bg-white px-4 py-3"
                >
                  <button
                    type="button"
                    onClick={() => setAssignedDetailId(s.id)}
                    className="text-left w-full"
                  >
                    <p className="text-sm font-semibold text-gray-900">
                      {s.hospital_name} · {formatRoleLabel(s.role_required)}
                    </p>
                    <p className="text-xs text-gray-600 mt-0.5">{nurseLifecycleLabel(s)}</p>
                    <p className="text-xs text-gray-500 mt-0.5">{activeShiftSummaryLine(s)}</p>
                  </button>
                  <ShiftDismissButton
                    shift={s}
                    onDismiss={handleDismissShift}
                    busy={dismissBusyId === s.id}
                  />
                </div>
              ))}
            </div>
          </div>
        )}

        {assignedDetailId && (
          <EmployeeShiftDetailSheet
            shiftId={assignedDetailId}
            mode="assigned"
            onClose={() => {
              setAssignedDetailId(null);
              fetchActiveShift();
            }}
          />
        )}

        {/* Pending Dispatch Offers — missed WebSocket deliveries recovered from API */}
        {isDispatchEligible && pendingOffers.length > 0 && (
          <div className="mb-6">
            <div className="flex items-center gap-2 mb-2">
              <div className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
              <h2 className="text-sm font-bold text-gray-900 uppercase tracking-wide">
                Urgent shift requests
              </h2>
              <span className="text-xs bg-red-100 text-red-700 px-1.5 py-0.5 rounded-full font-semibold">
                {pendingOffers.length}
              </span>
            </div>
            {offerError && (
              <p className="text-xs text-red-600 mb-2 bg-red-50 px-3 py-1.5 rounded-lg">{offerError}</p>
            )}
            <div className="flex flex-col gap-3">
              {pendingOffers.map((offer) => {
                const urgMeta = URGENCY_LABEL[offer.urgency] || URGENCY_LABEL.standard;
                const canAccept = offer.accept_eligible !== false;
                return (
                  <div
                    key={offer.offer_id}
                    className="bg-white rounded-2xl border border-orange-200 shadow-sm p-4"
                  >
                    <div className="flex items-start justify-between gap-3 mb-3">
                      <div className="min-w-0">
                        <p className="text-sm font-bold text-gray-900 truncate">{offer.hospital_name}</p>
                        <p className="text-xs text-gray-500 mt-0.5">
                          {formatRoleLabel(offer.role)} · starts {formatShiftTime(offer.shift_start)}
                        </p>
                        {offer.pay_rate && (
                          <p className="text-xs text-green-600 mt-0.5 font-medium">{offer.pay_rate}</p>
                        )}
                      </div>
                      <div className="shrink-0 flex flex-col items-end gap-1">
                        <span className={`text-xs px-2 py-0.5 rounded-full font-semibold ${urgMeta.color}`}>
                          {urgMeta.label}
                        </span>
                        <span className="text-xs text-amber-600">Open until shift starts</span>
                      </div>
                    </div>
                    {!canAccept && (
                      <p className="text-xs text-amber-800 bg-amber-50 border border-amber-100 rounded-lg px-3 py-2 mb-2">
                        {offer.accept_blocked_message || SHIFT_ACCEPT_NEARBY_ONLY_MSG}
                      </p>
                    )}
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleAcceptOffer(offer.offer_id)}
                        disabled={acceptingId === offer.offer_id || !canAccept}
                        className="flex-1 bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white font-semibold py-2.5 rounded-xl text-sm transition-colors"
                      >
                        {acceptingId === offer.offer_id ? 'Accepting…' : canAccept ? 'Accept Shift' : 'Nearby staff only'}
                      </button>
                      <button
                        onClick={() => handleDeclineOffer(offer.offer_id)}
                        disabled={acceptingId === offer.offer_id}
                        className="px-4 bg-gray-100 hover:bg-gray-200 disabled:opacity-50 text-gray-700 font-semibold py-2.5 rounded-xl text-sm transition-colors"
                      >
                        Decline
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-8">
          <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5">
            <p className="text-xs text-gray-400 mb-1">Phone</p>
            <p className="text-base font-semibold text-gray-800 truncate">{user?.phone || '—'}</p>
            {user?.role && (
              <span className="inline-block mt-2 text-xs bg-indigo-100 text-indigo-700 px-2 py-0.5 rounded-full font-medium">
                {user.role.replace('_', ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
              </span>
            )}
          </div>

          <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5">
            <p className="text-xs text-gray-400 mb-2">Profile Completion</p>
            <div className="flex items-end gap-2">
              <span className="text-3xl font-bold text-indigo-600">{completion}%</span>
            </div>
            <div className="mt-2 h-2 bg-gray-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-indigo-500 rounded-full transition-all"
                style={{ width: `${completion}%` }}
              />
            </div>
            {completion < 100 && (
              <button
                onClick={() => navigate('/profile')}
                className="mt-2 text-xs text-indigo-600 hover:underline"
              >
                Complete profile →
              </button>
            )}
          </div>

          <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5">
            <p className="text-xs text-gray-400 mb-1">Applications</p>
            <p className="text-3xl font-bold text-green-600">{applications.length}</p>
            <button
              onClick={() => navigate('/jobs')}
              className="mt-2 text-xs text-indigo-600 hover:underline"
            >
              Browse more jobs →
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
          <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-semibold text-gray-900">My Applications</h3>
              <button
                onClick={() => navigate('/jobs')}
                className="text-xs text-indigo-600 hover:underline"
              >
                Find Jobs
              </button>
            </div>
            {applications.length === 0 ? (
              <p className="text-sm text-gray-400 text-center py-6">No applications yet</p>
            ) : (
              <div className="flex flex-col gap-3">
                {applications.slice(0, 5).map((app) => (
                  <div key={app.id} className="flex items-center justify-between py-2 border-b border-gray-50 last:border-0">
                    <div>
                      <p className="text-sm font-medium text-gray-800">Job #{app.job_id}</p>
                      <p className="text-xs text-gray-400">{new Date(app.created_at).toLocaleDateString()}</p>
                    </div>
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${statusColors[app.status] || 'bg-gray-100 text-gray-600'}`}>
                      {app.status.charAt(0).toUpperCase() + app.status.slice(1)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-semibold text-gray-900">Quick Actions</h3>
            </div>
            <div className="flex flex-col gap-3">
              <button
                onClick={() => navigate('/profile')}
                className="w-full text-left flex items-center gap-3 p-3 rounded-xl hover:bg-gray-50 transition-colors"
              >
                <div className="w-9 h-9 bg-indigo-100 rounded-xl flex items-center justify-center text-indigo-600 shrink-0">
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                  </svg>
                </div>
                <div>
                  <p className="text-sm font-medium text-gray-800">Update Profile</p>
                  <p className="text-xs text-gray-400">Experience, skills, location</p>
                </div>
              </button>

              <button
                onClick={() => navigate('/profile')}
                className="w-full text-left flex items-center gap-3 p-3 rounded-xl hover:bg-gray-50 transition-colors"
              >
                <div className="w-9 h-9 bg-amber-100 rounded-xl flex items-center justify-center text-amber-600 shrink-0">
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2a1 1 0 01-.293.707L13 13.414V19a1 1 0 01-.553.894l-4 2A1 1 0 017 21v-7.586L3.293 6.707A1 1 0 013 6V4z" />
                  </svg>
                </div>
                <div>
                  <p className="text-sm font-medium text-gray-800">Job Preferences</p>
                  <p className="text-xs text-gray-400">India / Abroad / Passport</p>
                </div>
              </button>

              <button
                onClick={() => navigate('/jobs')}
                className="w-full text-left flex items-center gap-3 p-3 rounded-xl hover:bg-gray-50 transition-colors"
              >
                <div className="w-9 h-9 bg-green-100 rounded-xl flex items-center justify-center text-green-600 shrink-0">
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 13.255A23.931 23.931 0 0112 15c-3.183 0-6.22-.62-9-1.745M16 6V4a2 2 0 00-2-2h-4a2 2 0 00-2 2v2m4 6h.01M5 20h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                  </svg>
                </div>
                <div>
                  <p className="text-sm font-medium text-gray-800">Browse Jobs</p>
                  <p className="text-xs text-gray-400">Find your next opportunity</p>
                </div>
              </button>

              <button
                onClick={() => navigate('/resume')}
                className="w-full text-left flex items-center gap-3 p-3 rounded-xl hover:bg-gray-50 transition-colors"
              >
                <div className="w-9 h-9 bg-purple-100 rounded-xl flex items-center justify-center text-purple-600 shrink-0">
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                </div>
                <div>
                  <p className="text-sm font-medium text-gray-800">Build Resume</p>
                  <p className="text-xs text-gray-400">Create a professional resume</p>
                </div>
              </button>
            </div>
          </div>
        </div>
      </div>
    </MainLayout>
  );
}
