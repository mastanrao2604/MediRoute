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
} from '../utils/staffingStatusCopy';
import { useAreaLabel } from '../hooks/useAreaLabel';
import { shiftAreaSource } from '../utils/areaLabel';

function offerFromMyOffer(shift) {
  const mo = shift?.my_offer;
  if (!mo?.respondable || !mo.offer_id) return null;
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
    if (initialShift?.id === shiftId && initialShift?.my_offer?.respondable) {
      setShift(initialShift);
      setOffer(offerFromMyOffer(initialShift));
      setLoading(false);
    }
    load();
  }, [load, shiftId, initialShift]);

  async function handleAccept() {
    if (!offer || responding) return;
    setResponding(true);
    setActionError('');
    try {
      await api.post(`/dispatch/offers/${offer.offer_id}/accept`);
      onResponded?.('accepted');
      onClose();
    } catch (err) {
      setActionError(
        humanizeStaffingError(formatApiErrorDetail(err.response?.data?.detail) || 'Could not accept this shift.'),
      );
    } finally {
      setResponding(false);
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

  const isAssignedView = mode === 'assigned';
  const shiftOpen = shift ? isBeforeShiftStartUtc(shift.shift_start) : false;
  const canRespond = !isAssignedView && Boolean(offer && shiftOpen);
  const declinedEarlier = offer?.offer_status === 'declined';
  const assignmentStatus = shift?.assignment?.status;

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
                      {areaLabel || (shift.hospital_pincode ? `Pin ${shift.hospital_pincode}` : '—')}
                    </dd>
                  </div>
                )}
                {isAssignedView && assignmentStatus && (
                  <div>
                    <dt className="text-xs font-medium text-gray-400 uppercase tracking-wide">Your status</dt>
                    <dd className="text-gray-900 font-medium mt-0.5">
                      {nurseAssignmentStatusLabel(assignmentStatus)}
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
                {shift.notes && (
                  <div>
                    <dt className="text-xs font-medium text-gray-400 uppercase tracking-wide">Note from hospital</dt>
                    <dd className="text-gray-700 mt-0.5 whitespace-pre-line">{shift.notes}</dd>
                  </div>
                )}
              </dl>

              {canRespond && (
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

              {isAssignedView && (
                <div className="mt-4 rounded-xl bg-indigo-50 border border-indigo-200 px-3 py-3 text-sm text-indigo-900">
                  <p className="font-semibold">You are booked for this shift</p>
                  <p className="text-xs mt-1 text-indigo-800 leading-relaxed">
                    Arrive on time at the hospital. Contact them if your plans change.
                  </p>
                </div>
              )}

              {!isAssignedView && !canRespond && shiftOpen && !offer && shift.nearby_match && (
                <div className="mt-4 rounded-xl bg-amber-50 border border-amber-200 px-3 py-3 text-sm text-amber-900 leading-relaxed">
                  <p className="font-semibold">New nearby shift available</p>
                  <p className="mt-1 text-amber-800">
                    A hospital near you is looking for staff. Stay <strong>Available for Shifts</strong> on your
                    dashboard — you&apos;ll get a notification here when you can accept.
                  </p>
                </div>
              )}

              {!isAssignedView && !canRespond && shiftOpen && !offer && !shift.nearby_match && (
                <div className="mt-4 rounded-xl bg-slate-50 border border-slate-200 px-3 py-3 text-sm text-slate-700 leading-relaxed">
                  <p className="font-medium text-slate-900">Not in your matched area</p>
                  <p className="mt-1 text-slate-600">
                    This shift is outside your role, city, or service pincode. Update your profile pincode or check
                    shifts listed under <strong>Instant shifts</strong> on this page.
                  </p>
                </div>
              )}

              {!shiftOpen && (
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
            {canRespond ? (
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
                  disabled={responding}
                  className="min-h-[48px] rounded-xl bg-green-600 hover:bg-green-700 text-white font-bold text-sm disabled:opacity-50"
                >
                  {responding ? 'Accepting…' : 'Accept shift'}
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={onClose}
                className="col-span-2 min-h-[48px] rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white font-semibold text-sm"
              >
                Close
              </button>
            )}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

