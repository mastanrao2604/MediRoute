import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api/axios';
import MainLayout from '../layouts/MainLayout';
import Spinner from '../components/Spinner';
import AvailabilityToggle from '../components/AvailabilityToggle';
import EmployeeShiftDetailSheet from '../components/EmployeeShiftDetailSheet';
import { useAuth } from '../context/AuthContext';
import { formatShiftTime } from '../utils/shiftDateTime';
import { pickActiveNurseShift } from '../utils/nurseActiveShift';
import { URGENCY_LABEL, formatRoleLabel, humanizeStaffingError } from '../utils/staffingStatusCopy';
import { filterJobSeekerOffers } from '../utils/shiftVisibility';

const DISPATCH_ELIGIBLE_ROLES = new Set([
  'nurse', 'staff_nurse', 'icu_nurse', 'ot_nurse', 'emergency_nurse',
  'home_care_nurse', 'doctor', 'lab_tech', 'pharmacist', 'driver', 'front_office',
]);

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
  const [assignedDetailId, setAssignedDetailId] = useState(null);

  const isDispatchEligible = DISPATCH_ELIGIBLE_ROLES.has(user?.role);

  const fetchActiveShift = useCallback(async () => {
    if (!isDispatchEligible) return;
    try {
      const res = await api.get('/shifts/');
      setActiveShift(pickActiveNurseShift(res.data?.shifts));
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
    const onVisible = () => {
      if (document.visibilityState === 'visible') refresh();
    };
    document.addEventListener('visibilitychange', onVisible);
    const interval = setInterval(fetchActiveShift, 60000);
    return () => {
      window.removeEventListener('mr-nurse-active-shift-refresh', refresh);
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
      await fetchActiveShift();
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
        {DISPATCH_ELIGIBLE_ROLES.has(user?.role) && (
          <div className="mb-4">
            <AvailabilityToggle
              activeShift={activeShift}
              onOpenActiveShift={(shift) => setAssignedDetailId(shift.id)}
            />
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
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleAcceptOffer(offer.offer_id)}
                        disabled={acceptingId === offer.offer_id}
                        className="flex-1 bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white font-semibold py-2.5 rounded-xl text-sm transition-colors"
                      >
                        {acceptingId === offer.offer_id ? 'Accepting…' : 'Accept Shift'}
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
