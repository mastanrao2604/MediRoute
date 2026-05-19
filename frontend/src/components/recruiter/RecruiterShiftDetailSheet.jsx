import { useEffect, useState } from 'react';
import api from '../../api/axios';
import Spinner from '../Spinner';
import { formatApiErrorDetail } from '../../utils/apiErrorMessage';

const URGENCY_OPTIONS = [
  { value: 'emergency', label: 'Right Now' },
  { value: 'urgent', label: 'Within a Few Hours' },
  { value: 'standard', label: "Today's Shift" },
  { value: 'planned', label: 'Plan Ahead' },
];

function toLocalDatetimeValue(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function formatWhen(iso) {
  try {
    return new Date(iso).toLocaleString(undefined, {
      weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return iso ?? '—';
  }
}

export default function RecruiterShiftDetailSheet({
  shiftId, onClose, onUpdated, onCancel, onArchive, onRedispatch, busy,
}) {
  const [shift, setShift] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [form, setForm] = useState(null);

  useEffect(() => {
    if (!shiftId) return;
    setLoading(true);
    setError('');
    api.get(`/shifts/${shiftId}`)
      .then((res) => {
        const s = res.data?.shift;
        setShift(s);
        setForm({
          urgency: s.urgency || 'standard',
          shift_start: toLocalDatetimeValue(s.shift_start),
          shift_end: toLocalDatetimeValue(s.shift_end),
          notes: s.notes || '',
          pay_rate: s.pay_rate || '',
          specialty: s.specialty || '',
          dispatch_radius_km: String(s.dispatch_radius_km ?? 10),
        });
      })
      .catch((e) => setError(formatApiErrorDetail(e.response?.data?.detail) || 'Could not load shift.'))
      .finally(() => setLoading(false));
  }, [shiftId]);

  const editable = shift && (shift.status === 'open' || shift.status === 'dispatching');
  const canArchive = shift && (shift.status === 'cancelled' || shift.status === 'expired');
  const canCancel = shift && shift.status !== 'cancelled' && shift.status !== 'filled';
  const canRedispatch = shift && (shift.status === 'expired' || shift.status === 'cancelled');

  async function handleSave(e) {
    e.preventDefault();
    if (!editable || !form) return;
    setSaving(true);
    setError('');
    try {
      const payload = {
        urgency: form.urgency,
        shift_start: new Date(form.shift_start).toISOString(),
        notes: form.notes,
        pay_rate: form.pay_rate,
        specialty: form.specialty,
        dispatch_radius_km: parseFloat(form.dispatch_radius_km) || 10,
      };
      if (form.shift_end) payload.shift_end = new Date(form.shift_end).toISOString();
      const res = await api.patch(`/shifts/${shiftId}`, payload);
      setShift(res.data?.shift);
      onUpdated?.();
    } catch (err) {
      setError(formatApiErrorDetail(err.response?.data?.detail) || 'Could not save changes.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex flex-col justify-end bg-black/40" role="dialog" aria-modal="true">
      <button type="button" className="absolute inset-0" aria-label="Close" onClick={onClose} />
      <div className="relative bg-white rounded-t-2xl shadow-xl max-h-[90vh] overflow-y-auto pb-[env(safe-area-inset-bottom)]">
        <div className="sticky top-0 bg-white border-b border-gray-100 px-4 py-3 flex items-center justify-between z-10">
          <h2 className="text-lg font-bold text-gray-900">Shift details</h2>
          <button type="button" onClick={onClose} className="min-w-[44px] min-h-[44px] flex items-center justify-center text-gray-500 rounded-xl" aria-label="Close">✕</button>
        </div>
        <div className="px-4 py-4 pb-8">
          {loading && <div className="flex justify-center py-8"><Spinner /></div>}
          {error && !loading && <p className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded-lg mb-3">{error}</p>}
          {shift && !loading && (
            <>
              <div className="mb-4">
                <span className="text-xs font-semibold px-2 py-0.5 rounded-lg bg-slate-100 text-slate-700 capitalize">{shift.status}</span>
                <p className="text-base font-semibold text-gray-900 mt-2">{shift.hospital_name}</p>
                <p className="text-sm text-gray-500">{shift.role_required} · {formatWhen(shift.shift_start)}</p>
                {shift.dispatch && (
                  <p className="text-xs text-indigo-700 mt-1">Dispatch wave {shift.dispatch.current_wave}{shift.dispatch.waves_exhausted ? ' · waves exhausted' : ''}</p>
                )}
              </div>
              {editable && form ? (
                <form onSubmit={handleSave} className="flex flex-col gap-3">
                  <label className="block"><span className="text-xs font-medium text-gray-600">Urgency</span>
                    <select value={form.urgency} onChange={(e) => setForm((f) => ({ ...f, urgency: e.target.value }))} className="mt-1 w-full border border-gray-200 rounded-xl px-3 py-2 text-sm">
                      {URGENCY_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </label>
                  <label className="block"><span className="text-xs font-medium text-gray-600">Shift start</span>
                    <input type="datetime-local" value={form.shift_start} onChange={(e) => setForm((f) => ({ ...f, shift_start: e.target.value }))} className="mt-1 w-full border border-gray-200 rounded-xl px-3 py-2 text-sm" required />
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
                  <label className="block"><span className="text-xs font-medium text-gray-600">Search range — {form.dispatch_radius_km} km</span>
                    <input type="range" min="2" max="30" step="1" value={form.dispatch_radius_km} onChange={(e) => setForm((f) => ({ ...f, dispatch_radius_km: e.target.value }))} className="mt-1 w-full" />
                  </label>
                  <button type="submit" disabled={saving || busy} className="w-full bg-indigo-600 text-white font-semibold py-3 rounded-xl disabled:opacity-50">{saving ? 'Saving…' : 'Save changes'}</button>
                </form>
              ) : (
                <div className="text-sm text-gray-600 space-y-2 mb-4">
                  <p><span className="font-medium">Urgency:</span> {shift.urgency}</p>
                  <p><span className="font-medium">Pay:</span> {shift.pay_rate || '—'}</p>
                  <p><span className="font-medium">Notes:</span> {shift.notes || '—'}</p>
                  <p><span className="font-medium">Radius:</span> {shift.dispatch_radius_km} km</p>
                </div>
              )}
              <div className="flex flex-col gap-2 mt-4 pt-4 border-t border-gray-100">
                {canCancel && <button type="button" disabled={busy} onClick={() => onCancel?.(shiftId)} className="w-full text-sm font-semibold py-3 rounded-xl bg-red-50 text-red-700 border border-red-100">Cancel shift</button>}
                {canRedispatch && <button type="button" disabled={busy} onClick={() => onRedispatch?.(shiftId)} className="w-full text-sm font-semibold py-3 rounded-xl bg-indigo-600 text-white">Re-post dispatch</button>}
                {canArchive && <button type="button" disabled={busy} onClick={() => onArchive?.(shiftId)} className="w-full text-sm font-semibold py-3 rounded-xl bg-gray-100 text-gray-700">Delete from list</button>}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
