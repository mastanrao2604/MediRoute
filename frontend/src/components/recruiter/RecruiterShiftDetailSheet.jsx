import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import api from '../../api/axios';
import Spinner from '../Spinner';
import { formatApiErrorDetail } from '../../utils/apiErrorMessage';
import { useLockBodyScroll } from '../../hooks/useLockBodyScroll';
import { isPastShiftStart } from '../../utils/staffingStatusCopy';
import {
  formatShiftDateTime,
  toDatetimeLocalValue,
  datetimeLocalToUtcIso,
  nowDatetimeLocalPlusMinutes,
} from '../../utils/shiftDateTime';
import AssignedNurseCard from './AssignedNurseCard';
import ShiftApplicantsPanel from './ShiftApplicantsPanel';

const URGENCY_OPTIONS = [
  { value: 'emergency', label: 'Right Now' },
  { value: 'urgent', label: 'Within a Few Hours' },
  { value: 'standard', label: "Today's Shift" },
  { value: 'planned', label: 'Plan Ahead' },
];

export default function RecruiterShiftDetailSheet({
  shiftId,
  repostIntent = false,
  onClose,
  onUpdated,
  onCancel,
  onArchive,
  onRedispatch,
  onStopSearch,
  onConfirmStaff,
  confirmingNurseId = null,
  busy,
  onViewNurseProfile,
}) {
  const [shift, setShift] = useState(null);
  const [form, setForm] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useLockBodyScroll(Boolean(shiftId));

  useEffect(() => {
    if (!shiftId) return;
    setLoading(true);
    setError('');
    api.get(`/shifts/${shiftId}`)
      .then((res) => {
        const s = res.data?.shift;
        setShift(s);
        const past = isPastShiftStart(s.shift_start);
        let shiftStartLocal = toDatetimeLocalValue(s.shift_start);
        if (repostIntent && past) {
          shiftStartLocal = nowDatetimeLocalPlusMinutes(60);
        }
        setForm({
          urgency: s.urgency || 'standard',
          shift_start: shiftStartLocal,
          shift_end: toDatetimeLocalValue(s.shift_end),
          notes: s.notes || '',
          pay_rate: s.pay_rate || '',
          specialty: s.specialty || '',
          dispatch_radius_km: String(s.dispatch_radius_km ?? 10),
        });
      })
      .catch((e) => setError(formatApiErrorDetail(e.response?.data?.detail) || 'Could not load shift.'))
      .finally(() => setLoading(false));
  }, [shiftId, repostIntent]);

  useEffect(() => {
    if (!shiftId) return undefined;
    const reload = () => {
      api.get(`/shifts/${shiftId}`)
        .then((res) => setShift(res.data?.shift))
        .catch(() => {});
    };
    window.addEventListener('mr-recruiter-shifts-refresh', reload);
    return () => window.removeEventListener('mr-recruiter-shifts-refresh', reload);
  }, [shiftId]);

  const pastStart = isPastShiftStart(shift?.shift_start);
  const searchActive = shift?.search_active !== false && !shift?.search_closed;
  const isFilled = !searchActive && (shift?.status === 'filled' || Boolean(shift?.assignment));
  const editable = shift && (
    shift.status === 'open' ||
    shift.status === 'dispatching' ||
    shift.status === 'expired'
  );
  const canArchive = shift && (shift.status === 'cancelled' || shift.status === 'expired');
  const canCancel = shift && shift.status !== 'cancelled' && shift.status !== 'filled';
  const canRedispatch = shift && (shift.status === 'expired' || shift.status === 'cancelled');

  function buildPayload() {
    return {
      urgency: form.urgency,
      shift_start: datetimeLocalToUtcIso(form.shift_start),
      notes: form.notes,
      pay_rate: form.pay_rate,
      specialty: form.specialty,
      dispatch_radius_km: parseFloat(form.dispatch_radius_km) || 10,
      ...(form.shift_end ? { shift_end: datetimeLocalToUtcIso(form.shift_end) } : {}),
    };
  }

  async function handleSave(e, { thenRepost = false } = {}) {
    e.preventDefault();
    if (!editable || !form) return;
    setSaving(true);
    setError('');
    try {
      const startIso = datetimeLocalToUtcIso(form.shift_start);
      if (thenRepost && new Date(startIso).getTime() <= Date.now()) {
        setError('Please choose a new future shift time to repost this requirement.');
        return;
      }
      const res = await api.patch(`/shifts/${shiftId}`, buildPayload());
      setShift(res.data?.shift);
      await onUpdated?.();
      if (thenRepost) {
        await onRedispatch?.(shiftId);
        return;
      }
      onClose?.();
    } catch (err) {
      setError(formatApiErrorDetail(err.response?.data?.detail) || 'Could not save changes.');
    } finally {
      setSaving(false);
    }
  }

  if (!shiftId || typeof document === 'undefined') return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[100] flex flex-col justify-end"
      role="dialog"
      aria-modal="true"
      aria-label="Shift details"
    >
      <button
        type="button"
        className="absolute inset-0 bg-black/40"
        aria-label="Close"
        onClick={onClose}
      />
      <div
        className="relative z-[101] flex flex-col bg-white rounded-t-2xl shadow-2xl max-h-[min(88dvh,calc(100dvh-env(safe-area-inset-top,0px)-3.5rem))] lg:max-h-[90vh]"
        style={{ paddingBottom: 'var(--mobile-bottom-safe)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="shrink-0 flex items-center justify-between border-b border-gray-100 px-4 py-3 bg-white rounded-t-2xl">
          <h2 className="text-lg font-bold text-gray-900">
            {repostIntent ? 'Repost requirement' : 'Shift details'}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="min-w-[44px] min-h-[44px] flex items-center justify-center text-gray-500 rounded-xl hover:bg-gray-100"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain px-4 py-4">
          {loading && <div className="flex justify-center py-8"><Spinner /></div>}
          {error && !loading && <p className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded-lg mb-3">{error}</p>}
          {shift && !loading && (
            <>
              <div className="mb-4">
                <span className="text-xs font-semibold px-2 py-0.5 rounded-lg bg-slate-100 text-slate-700 capitalize">{shift.status}</span>
                <p className="text-base font-semibold text-gray-900 mt-2">{shift.hospital_name}</p>
                <p className="text-sm text-gray-500">{shift.role_required} · {formatShiftDateTime(shift.shift_start)}</p>
                {(shift?.applicants?.length > 0 || shift?.confirmed_count > 0) && (
                  <div className="mt-3">
                    <ShiftApplicantsPanel
                      shift={shift}
                      confirmingNurseId={confirmingNurseId}
                      onConfirmStaff={
                        onConfirmStaff
                          ? (nurse) => onConfirmStaff(shift.id, nurse.user_id)
                          : undefined
                      }
                      onViewProfile={(nurse) => onViewNurseProfile?.(nurse, shift)}
                    />
                  </div>
                )}
                {isFilled && !shift?.applicants?.length && (
                  <p className="text-xs text-green-800 font-semibold mt-2">Staff finalized</p>
                )}
                {searchActive && !pastStart && (
                  <p className="text-xs text-indigo-700 mt-1">
                    Still searching for available nurses
                  </p>
                )}
                {repostIntent && (
                  <div className="mt-2 rounded-xl bg-amber-50 border border-amber-200 px-3 py-2.5 text-sm text-amber-900">
                    <p className="font-semibold">Previous timing has passed</p>
                    <p className="text-xs mt-1 text-amber-800 leading-relaxed">
                      Choose a new future shift time below, update any details, then start searching again.
                    </p>
                  </div>
                )}
                {!repostIntent && (shift.status === 'expired' || (shift.status === 'dispatching' && pastStart)) && (
                  <p className="text-xs text-amber-800 mt-1">
                    No nurse confirmed in time. Update the start time below, then repost when ready.
                  </p>
                )}
              </div>
              {editable && form ? (
                <form onSubmit={handleSave} className="flex flex-col gap-3">
                  <label className="block"><span className="text-xs font-medium text-gray-600">Urgency</span>
                    <select value={form.urgency} onChange={(e) => setForm((f) => ({ ...f, urgency: e.target.value }))} className="mt-1 w-full border border-gray-200 rounded-xl px-3 py-2 text-sm">
                      {URGENCY_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </label>
                  <label className="block">
                    <span className={`text-xs font-medium ${repostIntent ? 'text-amber-900' : 'text-gray-600'}`}>
                      {repostIntent ? 'New shift time (required)' : 'Shift start'}
                    </span>
                    <input
                      type="datetime-local"
                      value={form.shift_start}
                      onChange={(e) => setForm((f) => ({ ...f, shift_start: e.target.value }))}
                      className={`mt-1 w-full border rounded-xl px-3 py-2 text-sm ${
                        repostIntent ? 'border-amber-300 ring-1 ring-amber-200 bg-amber-50/40' : 'border-gray-200'
                      }`}
                      required
                    />
                  </label>
                  <label className="block"><span className="text-xs font-medium text-gray-600">Shift end (optional)</span>
                    <input type="datetime-local" value={form.shift_end} onChange={(e) => setForm((f) => ({ ...f, shift_end: e.target.value }))} className="mt-1 w-full border border-gray-200 rounded-xl px-3 py-2 text-sm" />
                  </label>
                  <label className="block"><span className="text-xs font-medium text-gray-600">Pay rate</span>
                    <input type="text" value={form.pay_rate} onChange={(e) => setForm((f) => ({ ...f, pay_rate: e.target.value }))} className="mt-1 w-full border border-gray-200 rounded-xl px-3 py-2 text-sm" />
                  </label>
                  <label className="block"><span className="text-xs font-medium text-gray-600">Specialty / notes</span>
                    <input type="text" value={form.specialty} onChange={(e) => setForm((f) => ({ ...f, specialty: e.target.value }))} className="mt-1 w-full border border-gray-200 rounded-xl px-3 py-2 text-sm mb-2" />
                    <textarea value={form.notes} onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))} rows={2} className="w-full border border-gray-200 rounded-xl px-3 py-2 text-sm" placeholder="Notes for nurses" />
                  </label>
                  <label className="block"><span className="text-xs font-medium text-gray-600">Search distance — {form.dispatch_radius_km} km</span>
                    <input type="range" min="2" max="30" step="1" value={form.dispatch_radius_km} onChange={(e) => setForm((f) => ({ ...f, dispatch_radius_km: e.target.value }))} className="mt-1 w-full" />
                  </label>
                  <div className="flex flex-col gap-2">
                    {repostIntent ? (
                      <>
                        <button
                          type="button"
                          disabled={saving || busy}
                          onClick={(e) => handleSave(e, { thenRepost: true })}
                          className="w-full bg-indigo-600 text-white font-semibold py-3 rounded-xl disabled:opacity-50"
                        >
                          {saving ? 'Starting search…' : 'Save & start searching again'}
                        </button>
                        <button
                          type="submit"
                          disabled={saving || busy}
                          className="w-full bg-white text-indigo-700 font-semibold py-3 rounded-xl border border-indigo-200 disabled:opacity-50"
                        >
                          {saving ? 'Saving…' : 'Save changes only'}
                        </button>
                      </>
                    ) : (
                      <button type="submit" disabled={saving || busy} className="w-full bg-indigo-600 text-white font-semibold py-3 rounded-xl disabled:opacity-50">
                        {saving ? 'Saving…' : 'Save & close'}
                      </button>
                    )}
                    <button
                      type="button"
                      disabled={saving || busy}
                      onClick={() => onClose?.()}
                      className="w-full bg-gray-100 text-gray-700 font-semibold py-3 rounded-xl disabled:opacity-50"
                    >
                      Cancel
                    </button>
                  </div>
                </form>
              ) : (
                <div className="text-sm text-gray-600 space-y-2 mb-4">
                  <p><span className="font-medium">Urgency:</span> {shift.urgency}</p>
                  <p><span className="font-medium">Pay:</span> {shift.pay_rate || '—'}</p>
                  <p><span className="font-medium">Notes:</span> {shift.notes || '—'}</p>
                  <p><span className="font-medium">Search distance:</span> {shift.dispatch_radius_km} km</p>
                </div>
              )}
              <div className="flex flex-col gap-2 mt-4 pt-4 border-t border-gray-100 pb-2">
                {searchActive && !pastStart && onStopSearch && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => onStopSearch?.(shiftId)}
                    className="w-full text-sm font-semibold py-3 rounded-xl bg-amber-50 text-amber-900 border border-amber-200"
                  >
                    Stop searching
                  </button>
                )}
                {canCancel && (
                  <button type="button" disabled={busy} onClick={() => onCancel?.(shiftId)} className="w-full text-sm font-semibold py-3 rounded-xl bg-red-50 text-red-700 border border-red-100">
                    Cancel shift
                  </button>
                )}
                {canRedispatch && !repostIntent && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => onRedispatch?.(shiftId)}
                    className="w-full text-sm font-semibold py-3 rounded-xl bg-indigo-600 text-white"
                  >
                    Start searching again
                  </button>
                )}
                {canArchive && (
                  <button type="button" disabled={busy} onClick={() => onArchive?.(shiftId)} className="w-full text-sm font-semibold py-3 rounded-xl bg-gray-100 text-gray-700">
                    Delete from list
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
