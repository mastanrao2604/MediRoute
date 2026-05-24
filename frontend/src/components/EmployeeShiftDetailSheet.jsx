/**
 * Employee shift detail — tap from Jobs list.
 * Accept / Decline until shift start; may accept again after declining.
 */
import { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import api from '../api/axios';
import Spinner from './Spinner';
import { useLockBodyScroll } from '../hooks/useLockBodyScroll';
import { formatShiftDateTime, isBeforeShiftStartUtc } from '../utils/shiftDateTime';
import { formatApiErrorDetail } from '../utils/apiErrorMessage';
import {
  formatRoleLabel,
  humanizeStaffingError,
  nurseAssignmentStatusLabel,
  shiftStatusLabel,
  urgencyLabel,
  isApplicationFinalized,
  isApplicationPending,
  isShiftCancelledForNurse,
  cancelledShiftStatusLabel,
  APPLICATION_STATUS_LABEL,
  nurseLifecycleLabel,
} from '../utils/staffingStatusCopy';
import { useAreaLabel } from '../hooks/useAreaLabel';
import { formatAreaDisplaySync, shiftAreaSource } from '../utils/areaLabel';
import {
  shiftCanAccept,
  SHIFT_ACCEPT_NEARBY_ONLY_MSG,
} from '../utils/shiftVisibility';
import { triggerDispatchReconcile } from '../utils/dispatchReconcile';

const ACTIVE_ASSIGNMENT = new Set(['confirmed', 'checked_in']);

function readGpsPosition() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error('Location is not available on this device.'));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({
        latitude: pos.coords.latitude,
        longitude: pos.coords.longitude,
      }),
      (err) => reject(err),
      { enableHighAccuracy: true, timeout: 20000, maximumAge: 60000 },
    );
  });
}

function offerFromMyOffer(shift) {
  const mo = shift?.my_offer;
  if (!mo?.offer_id || !mo.respondable) return null;
  return {
    offer_id: mo.offer_id,
    shift_id: shift.id,
    offer_status: mo.status,
    hospital_name: shift.hospital_name,
    role: shift.role_required,
    urgency: shift.urgency,
    shift_start: shift.shift_start,
    pay_rate: shift.pay_rate,
  };
}

function hasActiveAssignment(shift) {
  if (isShiftCancelledForNurse(shift)) return false;
  const st = shift?.assignment?.status;
  if (st === 'completed' || st === 'no_show') return false;
  return st && ACTIVE_ASSIGNMENT.has(st);
}

function hasBookedAssignment(shift) {
  if (isShiftCancelledForNurse(shift)) return false;
  const st = shift?.assignment?.status;
  return Boolean(st && st !== 'cancelled');
}


export default function EmployeeShiftDetailSheet({
  shiftId,
  onClose,
  onResponded,
  onUnavailable,
  mode = 'browse',
  initialShift = null,
}) {
  const [shift, setShift] = useState(null);
  const [offer, setOffer] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');
  const [responding, setResponding] = useState(false);
  const [acceptPhase, setAcceptPhase] = useState(null);
  const [operating, setOperating] = useState(false);

  useLockBodyScroll(Boolean(shiftId));

  const load = useCallback(async () => {
    if (!shiftId) return;
    setLoading(true);
    setError('');
    try {
      const shiftRes = await api.get(`/shifts/${shiftId}`);
      const loaded = shiftRes.data?.shift ?? null;
      setShift(loaded);
      if (mode === 'browse') {
        let matched = offerFromMyOffer(loaded);
        if (!matched) {
          const offersRes = await api.get('/dispatch/offers/pending');
          const pending = offersRes.data?.offers || [];
          matched = pending.find((o) => o.shift_id === shiftId) || null;
        }
        setOffer(matched);
      } else {
        setOffer(null);
      }
    } catch (err) {
      const status = err.response?.status;
      const detail = formatApiErrorDetail(err.response?.data?.detail);
      const unavailable = status === 404 || status === 403;
      setError(
        humanizeStaffingError(
          unavailable ? (detail || 'This shift is no longer available.') : (detail || 'Could not load shift details.'),
        ),
      );
      if (unavailable) onUnavailable?.();
    } finally {
      setLoading(false);
    }
  }, [shiftId, mode, onUnavailable]);

  useEffect(() => {
    if (initialShift?.id === shiftId) {
      setShift(initialShift);
      setOffer(offerFromMyOffer(initialShift));
      if (hasActiveAssignment(initialShift)) {
        setAcceptPhase(isApplicationFinalized(initialShift) ? 'confirmed' : 'applied');
      }
      setLoading(false);
    }
    if (shiftId && mode === 'assigned') {
      triggerDispatchReconcile('shift_detail_open').catch(() => {});
    }
    load();
  }, [load, shiftId, initialShift, mode]);

  useEffect(() => {
    if (!shiftId) return undefined;
    const onRefresh = () => load();
    window.addEventListener('mr-nurse-active-shift-refresh', onRefresh);
    return () => window.removeEventListener('mr-nurse-active-shift-refresh', onRefresh);
  }, [shiftId, load]);

  async function waitForConfirmation() {
    for (let i = 0; i < 20; i += 1) {
      try {
        const shiftRes = await api.get(`/shifts/${shiftId}`);
        const loaded = shiftRes.data?.shift ?? null;
        if (loaded) {
          setShift(loaded);
          if (hasActiveAssignment(loaded) && isApplicationFinalized(loaded)) {
            setOffer(null);
            setAcceptPhase('confirmed');
            onResponded?.('confirmed');
            return;
          }
          if (hasActiveAssignment(loaded)) {
            setAcceptPhase('applied');
            return;
          }
          if (loaded.my_offer?.status === 'accepted' && !loaded.my_offer?.respondable) {
            setOffer(null);
            setAcceptPhase('confirming');
          }
        }
      } catch {
        /* retry */
      }
      await new Promise((r) => setTimeout(r, 500));
    }
  }

  async function handleAccept() {
    if (!offer || responding || acceptPhase) return;
    setResponding(true);
    setActionError('');
    try {
      await api.post(`/dispatch/offers/${offer.offer_id}/accept`);
      setOffer(null);
      setAcceptPhase('applied');
      await waitForConfirmation();
    } catch (err) {
      setAcceptPhase(null);
      setActionError(
        humanizeStaffingError(formatApiErrorDetail(err.response?.data?.detail) || 'Could not accept this shift.'),
      );
      await load();
    } finally {
      setResponding(false);
    }
  }

  async function handleCheckIn() {
    if (operating || !shiftId) return;
    setOperating(true);
    setActionError('');
    try {
      const { latitude, longitude } = await readGpsPosition();
      await api.post(`/shifts/${shiftId}/checkin`, { latitude, longitude });
      await load();
      window.dispatchEvent(new CustomEvent('mr-nurse-active-shift-refresh'));
    } catch (err) {
      const detail = formatApiErrorDetail(err.response?.data?.detail);
      if (err?.code === 1 || err?.message?.includes('denied')) {
        setActionError('Location permission is required to check in near the hospital.');
      } else {
        setActionError(humanizeStaffingError(detail || err.message || 'Could not check in.'));
      }
    } finally {
      setOperating(false);
    }
  }

  async function handleCheckOut() {
    if (operating || !shiftId) return;
    setOperating(true);
    setActionError('');
    try {
      await api.post(`/shifts/${shiftId}/checkout`);
      await load();
      window.dispatchEvent(new CustomEvent('mr-nurse-active-shift-refresh'));
      onResponded?.('completed');
    } catch (err) {
      setActionError(
        humanizeStaffingError(formatApiErrorDetail(err.response?.data?.detail) || 'Could not check out.'),
      );
    } finally {
      setOperating(false);
    }
  }

  async function handleDecline() {
    if (!offer || responding) return;
    setResponding(true);
    setActionError('');
    try {
      await api.post(`/dispatch/offers/${offer.offer_id}/decline`);
      onResponded?.('declined');
      await load();
    } catch {
      await load();
    } finally {
      setResponding(false);
    }
  }

  const areaLabel = useAreaLabel(shift ? shiftAreaSource(shift) : {});

  if (!shiftId || typeof document === 'undefined') return null;

  const isCancelled = isShiftCancelledForNurse(shift);
  const isAssignedView = (mode === 'assigned' || hasBookedAssignment(shift)) && !isCancelled;
  const shiftOpen = shift ? isBeforeShiftStartUtc(shift.shift_start) : false;
  const isConfirmed =
    !isCancelled
    && (acceptPhase === 'confirmed'
    || (hasActiveAssignment(shift) && isApplicationFinalized(shift)));
  const isPendingReview =
    !isCancelled
    && (acceptPhase === 'applied' || isApplicationPending(shift))
    && !isConfirmed;
  const assignmentStatus = shift?.assignment?.status;
  const lifecycleStage = shift?.assignment?.lifecycle_stage;
  const isCheckedIn = assignmentStatus === 'checked_in' || lifecycleStage === 'checked_in';
  const isCompleted = assignmentStatus === 'completed' || lifecycleStage === 'completed';
  const canCheckIn =
    isAssignedView
    && isApplicationFinalized(shift)
    && assignmentStatus === 'confirmed'
    && !isCheckedIn
    && !isCompleted;
  const canCheckOut = isAssignedView && isCheckedIn;
  const canRespond =
    !isAssignedView &&
    !isConfirmed &&
    !isPendingReview &&
    !isCompleted &&
    Boolean(shift?.my_offer?.respondable && offer && shiftOpen);
  const canAccept = canRespond && shiftCanAccept(shift);
  const acceptBlockedMsg =
    shift?.accept_blocked_message || shift?.my_offer?.accept_blocked_message || SHIFT_ACCEPT_NEARBY_ONLY_MSG;
  const declinedEarlier = offer?.offer_status === 'declined' || shift?.my_offer?.status === 'declined';
  const statusLabel = shift ? nurseLifecycleLabel(shift) : '';

  return createPortal(
    <div
      className="fixed inset-0 z-[100] flex flex-col justify-end"
      role="dialog"
      aria-modal="true"
      aria-label="Shift details"
    >
      <button type="button" className="absolute inset-0 bg-black/40" aria-label="Close" onClick={onClose} />
      <div
        className="relative z-[101] flex flex-col bg-white rounded-t-2xl shadow-2xl max-h-[min(88dvh,calc(100dvh-env(safe-area-inset-top,0px)-3.5rem))]"
        style={{ paddingBottom: 'var(--mobile-bottom-safe)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="shrink-0 flex items-center justify-between border-b border-gray-100 px-4 py-3">
          <h2 className="text-lg font-bold text-gray-900">
            {isAssignedView ? 'Your shift' : 'Shift details'}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="min-w-[44px] min-h-[44px] flex items-center justify-center text-gray-500 rounded-xl hover:bg-gray-100"
            aria-label="Close"
          >
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden>
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain px-4 py-4">
          {loading && (
            <div className="flex justify-center py-12"><Spinner /></div>
          )}
          {error && !loading && (
            <p className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded-lg">{error}</p>
          )}
          {shift && !loading && (
            <>
              <div className="flex flex-wrap gap-2 mb-3">
                <span className="text-xs font-semibold px-2.5 py-1 rounded-full bg-amber-50 text-amber-900">
                  Instant shift
                </span>
                <span className="text-xs font-semibold px-2.5 py-1 rounded-full bg-slate-100 text-slate-700">
                  {shiftStatusLabel(shift.status)}
                </span>
                <span className="text-xs font-semibold px-2.5 py-1 rounded-full bg-indigo-50 text-indigo-800">
                  {urgencyLabel(shift.urgency)}
                </span>
              </div>

              <h3 className="text-xl font-bold text-gray-900">{shift.hospital_name || 'Hospital'}</h3>
              <p className="text-sm text-gray-600 mt-1">
                {formatRoleLabel(shift.role_required)}
                {shift.specialty ? ` · ${shift.specialty}` : ''}
              </p>

              <dl className="mt-4 space-y-3 text-sm">
                <div>
                  <dt className="text-xs font-medium text-gray-400 uppercase tracking-wide">Starts</dt>
                  <dd className="text-gray-900 font-medium mt-0.5">{formatShiftDateTime(shift.shift_start)}</dd>
                </div>
                {shift.shift_end && (
                  <div>
                    <dt className="text-xs font-medium text-gray-400 uppercase tracking-wide">Ends</dt>
                    <dd className="text-gray-900 font-medium mt-0.5">{formatShiftDateTime(shift.shift_end)}</dd>
                  </div>
                )}
                {shift.pay_rate && (
                  <div>
                    <dt className="text-xs font-medium text-gray-400 uppercase tracking-wide">Pay</dt>
                    <dd className="text-green-700 font-medium mt-0.5">{shift.pay_rate}</dd>
                  </div>
                )}
                {(areaLabel || shift.hospital_pincode) && (
                  <div>
                    <dt className="text-xs font-medium text-gray-400 uppercase tracking-wide">Area</dt>
                    <dd className="text-gray-900 mt-0.5">
                      {areaLabel ||
                        formatAreaDisplaySync(shiftAreaSource(shift)) ||
                        '—'}
                    </dd>
                  </div>
                )}
                {isAssignedView && (statusLabel || assignmentStatus) && (
                  <div>
                    <dt className="text-xs font-medium text-gray-400 uppercase tracking-wide">Your status</dt>
                    <dd className="text-gray-900 font-medium mt-0.5">
                      {statusLabel || nurseAssignmentStatusLabel(assignmentStatus)}
                    </dd>
                  </div>
                )}
                {isAssignedView && shift.assignment?.check_in_at && (
                  <div>
                    <dt className="text-xs font-medium text-gray-400 uppercase tracking-wide">Checked in</dt>
                    <dd className="text-gray-900 font-medium mt-0.5">
                      {formatShiftDateTime(shift.assignment.check_in_at)}
                    </dd>
                  </div>
                )}
                {isAssignedView && shift.assignment?.check_out_at && (
                  <div>
                    <dt className="text-xs font-medium text-gray-400 uppercase tracking-wide">Checked out</dt>
                    <dd className="text-gray-900 font-medium mt-0.5">
                      {formatShiftDateTime(shift.assignment.check_out_at)}
                    </dd>
                  </div>
                )}
                {shift.notes && (
                  <div>
                    <dt className="text-xs font-medium text-gray-400 uppercase tracking-wide">Note from hospital</dt>
                    <dd className="text-gray-700 mt-0.5 whitespace-pre-line">{shift.notes}</dd>
                  </div>
                )}
              </dl>

              {isCancelled && (
                <div className="mt-4 rounded-xl bg-red-50 border border-red-200 px-3 py-3">
                  <p className="text-sm font-semibold text-red-900">Shift cancelled</p>
                  <p className="text-xs text-red-800 mt-1">
                    {cancelledShiftStatusLabel(shift)}. You can accept other open shifts.
                  </p>
                </div>
              )}

              {isPendingReview && (
                <div className="mt-4 rounded-xl bg-amber-50 border border-amber-200 px-3 py-3">
                  <p className="text-sm font-semibold text-amber-900">Application submitted</p>
                  <p className="text-xs text-amber-800 mt-1">
                    {APPLICATION_STATUS_LABEL.applied}. You will be notified when the hospital confirms.
                  </p>
                </div>
              )}

              {isConfirmed && !isCheckedIn && !isCompleted && (
                <div className="mt-4 rounded-xl bg-green-50 border border-green-200 px-3 py-3">
                  <p className="text-sm font-semibold text-green-900">Shift confirmed</p>
                  <p className="text-xs text-green-800 mt-1">
                    {APPLICATION_STATUS_LABEL.confirmed}. Check in when you arrive at the hospital (within 200m).
                  </p>
                </div>
              )}

              {isCheckedIn && !isCompleted && (
                <div className="mt-4 rounded-xl bg-blue-50 border border-blue-200 px-3 py-3">
                  <p className="text-sm font-semibold text-blue-900">On shift</p>
                  <p className="text-xs text-blue-800 mt-1">
                    You are checked in. Check out when your shift ends to become available again.
                  </p>
                </div>
              )}

              {isCompleted && (
                <div className="mt-4 rounded-xl bg-slate-50 border border-slate-200 px-3 py-3">
                  <p className="text-sm font-semibold text-slate-900">Shift completed</p>
                  <p className="text-xs text-slate-700 mt-1">
                    This shift is complete. You can accept new shifts from your dashboard.
                  </p>
                </div>
              )}

              {canCheckIn && (
                <div className="mt-4 rounded-xl bg-indigo-50 border border-indigo-200 px-3 py-3 text-xs text-indigo-900">
                  Check-in uses your phone location and must be within 200m of the hospital.
                </div>
              )}

              {shift.hospital_contact && (
                <div className="mt-4 rounded-xl border border-gray-100 bg-gray-50 px-3 py-3">
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Hospital</p>
                  <p className="text-sm font-medium text-gray-900 mt-1">
                    {shift.hospital_contact.hospital_name}
                    {shift.hospital_contact.company_name
                      ? ` · ${shift.hospital_contact.company_name}`
                      : ''}
                  </p>
                  {shift.hospital_contact.locality && (
                    <p className="text-xs text-gray-600 mt-0.5">{shift.hospital_contact.locality}</p>
                  )}
                  {shift.hospital_contact.phone && (
                    <a
                      href={`tel:${shift.hospital_contact.phone}`}
                      className="inline-flex mt-2 text-sm font-semibold text-indigo-700"
                    >
                      Call hospital · {shift.hospital_contact.phone}
                    </a>
                  )}
                  {!shift.hospital_contact.phone && isPendingReview && (
                    <p className="text-xs text-gray-500 mt-1">
                      Hospital phone will appear after they confirm your application.
                    </p>
                  )}
                </div>
              )}

              {canRespond && canAccept && (
                <div className="mt-4 rounded-xl bg-green-50 border border-green-200 px-3 py-3">
                  <p className="text-sm font-semibold text-green-900">Ready to accept this shift</p>
                  <p className="text-xs text-green-800 mt-1">
                    You can accept or decline any time before the shift starts
                    ({formatShiftDateTime(shift.shift_start)}).
                  </p>
                  {declinedEarlier && (
                    <p className="text-xs text-green-800 mt-2 font-medium">
                      You declined earlier — you can still accept if you change your mind.
                    </p>
                  )}
                </div>
              )}

              {isAssignedView && !isCheckedIn && !isCompleted && (
                <div className="mt-4 rounded-xl bg-indigo-50 border border-indigo-200 px-3 py-3 text-sm text-indigo-900">
                  <p className="font-semibold">You are booked for this shift</p>
                  <p className="text-xs mt-1 text-indigo-800 leading-relaxed">
                    Arrive on time at the hospital. Contact them if your plans change.
                  </p>
                </div>
              )}

              {canRespond && !canAccept && (
                <div className="mt-4 rounded-xl bg-amber-50 border border-amber-200 px-3 py-3">
                  <p className="text-sm font-semibold text-amber-900">Shift visible in your area</p>
                  <p className="text-xs text-amber-800 mt-1 leading-relaxed">
                    {acceptBlockedMsg}
                  </p>
                  <p className="text-xs text-amber-700 mt-2">
                    Nearby nurses are being contacted first. You can still decline if you received an invitation.
                  </p>
                </div>
              )}

              {!isAssignedView && !canRespond && shiftOpen && !offer && (
                <div className="mt-4 rounded-xl bg-amber-50 border border-amber-200 px-3 py-3 text-sm text-amber-900 leading-relaxed">
                  <p className="font-semibold">New shift available</p>
                  <p className="mt-1 text-amber-800">
                    A hospital is looking for staff in your city. Stay <strong>Available for Shifts</strong> on your
                    dashboard — you&apos;ll get a notification when you can respond.
                  </p>
                </div>
              )}

              {!shiftOpen && !isAssignedView && (
                <div className="mt-4 rounded-xl bg-amber-50 border border-amber-200 px-3 py-3 text-sm text-amber-900">
                  This shift has already started or is no longer open for responses.
                </div>
              )}

              {actionError && (
                <p className="mt-3 text-sm text-red-600 bg-red-50 px-3 py-2 rounded-lg">{actionError}</p>
              )}
            </>
          )}
        </div>

        {shift && !loading && (
          <div className="shrink-0 border-t border-gray-100 px-4 pt-3 pb-2 grid grid-cols-2 gap-3">
            {canCheckIn ? (
              <button
                type="button"
                onClick={handleCheckIn}
                disabled={operating}
                className="col-span-2 min-h-[48px] rounded-xl bg-green-600 hover:bg-green-700 text-white font-bold text-sm disabled:opacity-50"
              >
                {operating ? 'Checking in…' : 'Check in at hospital'}
              </button>
            ) : canCheckOut ? (
              <button
                type="button"
                onClick={handleCheckOut}
                disabled={operating}
                className="col-span-2 min-h-[48px] rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-sm disabled:opacity-50"
              >
                {operating ? 'Checking out…' : 'Check out · complete shift'}
              </button>
            ) : canRespond ? (
              <>
                <button
                  type="button"
                  onClick={handleDecline}
                  disabled={responding}
                  className="min-h-[48px] rounded-xl border border-gray-200 text-gray-700 font-semibold text-sm hover:bg-gray-50 disabled:opacity-50"
                >
                  Decline
                </button>
                <button
                  type="button"
                  onClick={handleAccept}
                  disabled={responding || !canAccept}
                  className="min-h-[48px] rounded-xl bg-green-600 hover:bg-green-700 text-white font-bold text-sm disabled:opacity-50"
                >
                  {responding ? 'Accepting…' : canAccept ? 'Accept shift' : 'Nearby staff only'}
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={onClose}
                disabled={responding || operating}
                className="col-span-2 min-h-[48px] rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white font-semibold text-sm disabled:opacity-50"
              >
                {isCompleted ? 'Done' : isCheckedIn ? 'Close' : isConfirmed ? 'Done' : 'Close'}
              </button>
            )}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

