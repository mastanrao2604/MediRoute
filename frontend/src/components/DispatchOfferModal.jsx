/**
 * DispatchOfferModal — full-screen shift invitation for nurses.
 * No countdown: nurses may accept or decline until shift start.
 */
import { useState, useCallback } from 'react';
import { createPortal } from 'react-dom';
import api from '../api/axios';
import { useLockBodyScroll } from '../hooks/useLockBodyScroll';
import { formatShiftDateTime } from '../utils/shiftDateTime';
import { formatRoleLabel, humanizeStaffingError, urgencyLabel } from '../utils/staffingStatusCopy';
import { SHIFT_ACCEPT_NEARBY_ONLY_MSG } from '../utils/shiftVisibility';

const URGENCY_COLORS = {
  emergency: { bg: 'bg-red-600', ring: 'ring-red-400', text: 'text-red-100', badge: 'bg-red-800' },
  urgent: { bg: 'bg-orange-500', ring: 'ring-orange-300', text: 'text-orange-50', badge: 'bg-orange-700' },
  standard: { bg: 'bg-indigo-600', ring: 'ring-indigo-400', text: 'text-indigo-100', badge: 'bg-indigo-800' },
  planned: { bg: 'bg-teal-600', ring: 'ring-teal-400', text: 'text-teal-100', badge: 'bg-teal-800' },
};

export default function DispatchOfferModal({ offer, onClose }) {
  const [responding, setResponding] = useState(false);
  const [result, setResult] = useState(null);
  const [errorMsg, setErrorMsg] = useState('');

  useLockBodyScroll(Boolean(offer));

  const canAccept = offer?.accept_eligible !== false;
  const acceptBlockedMsg =
    offer?.accept_blocked_message || SHIFT_ACCEPT_NEARBY_ONLY_MSG;

  const handleAccept = useCallback(async () => {
    if (responding || result || offer?.accept_eligible === false) return;
    setResponding(true);
    try {
      await api.post(`/dispatch/offers/${offer.offer_id}/accept`);
      setResult('accepted');
      window.dispatchEvent(new CustomEvent('mr-nurse-active-shift-refresh'));
      window.dispatchEvent(new CustomEvent('mr-jobs-shifts-refresh'));
      setTimeout(onClose, 2500);
    } catch (err) {
      const detail = err.response?.data?.detail;
      setErrorMsg(
        humanizeStaffingError(
          typeof detail === 'string' ? detail : 'Could not accept this shift. Please try again.',
        ),
      );
      setResult('error');
      setTimeout(onClose, 3500);
    } finally {
      setResponding(false);
    }
  }, [offer?.offer_id, responding, result, onClose]);

  const handleDecline = useCallback(async () => {
    if (responding || result) return;
    setResponding(true);
    try {
      await api.post(`/dispatch/offers/${offer.offer_id}/decline`);
    } catch {
      // best-effort
    } finally {
      setResult('declined');
      setResponding(false);
      setTimeout(onClose, 1500);
    }
  }, [offer?.offer_id, responding, result, onClose]);

  if (!offer || typeof document === 'undefined') return null;

  const urgency = offer.urgency || 'standard';
  const colors = URGENCY_COLORS[urgency] || URGENCY_COLORS.standard;

  let content;

  if (result === 'accepted') {
    content = (
      <div
        className="fixed inset-0 z-[100] flex items-center justify-center bg-green-700"
        style={{ paddingTop: 'env(safe-area-inset-top, 0px)', paddingBottom: 'var(--mobile-bottom-safe)' }}
      >
        <div className="text-center text-white px-6 max-w-sm">
          <h2 className="text-2xl font-bold mb-1">Shift confirmed</h2>
          <p className="text-green-100 text-base mt-1">{offer.hospital_name}</p>
          <p className="text-green-200 text-sm mt-1">{formatShiftDateTime(offer.shift_start)}</p>
        </div>
      </div>
    );
  } else if (result === 'declined') {
    content = (
      <div
        className="fixed inset-0 z-[100] flex items-center justify-center bg-gray-700"
        style={{ paddingTop: 'env(safe-area-inset-top, 0px)', paddingBottom: 'var(--mobile-bottom-safe)' }}
      >
        <div className="text-center text-white px-6">
          <h2 className="text-lg font-semibold text-gray-200">Invitation declined</h2>
          <p className="text-gray-400 text-sm mt-2">You can still accept from Jobs until the shift starts.</p>
        </div>
      </div>
    );
  } else if (result === 'error') {
    content = (
      <div
        className="fixed inset-0 z-[100] flex items-center justify-center bg-gray-800"
        style={{ paddingTop: 'env(safe-area-inset-top, 0px)', paddingBottom: 'var(--mobile-bottom-safe)' }}
      >
        <div className="text-center text-white px-6 max-w-sm">
          <h2 className="text-lg font-semibold mb-2">Unable to accept</h2>
          <p className="text-gray-300 text-sm">{errorMsg}</p>
        </div>
      </div>
    );
  } else {
    content = (
      <div
        className="fixed inset-0 z-[100] flex flex-col justify-end"
        role="dialog"
        aria-modal="true"
        aria-label="Urgent shift invitation"
      >
        <div className="absolute inset-0 bg-black/50" aria-hidden="true" />

        <div
          className={`relative z-[101] flex flex-col rounded-t-2xl shadow-2xl overflow-hidden ${colors.bg}`}
          style={{
            maxHeight: 'min(88dvh, calc(100dvh - env(safe-area-inset-top, 0px) - 0.5rem))',
            paddingBottom: 'var(--mobile-bottom-safe)',
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between px-4 pt-2.5 pb-1 shrink-0">
            <div className="w-6" />
            <div className="w-10 h-1 bg-white/30 rounded-full" />
            <button
              type="button"
              onClick={onClose}
              className="w-10 h-10 flex items-center justify-center text-white/60 hover:text-white"
              title="Dismiss — respond later from Jobs or Dashboard"
              aria-label="Dismiss invitation"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain px-5 pt-4 pb-3">
            <div className={`inline-block text-xs font-bold px-3 py-1 rounded-full ${colors.badge} ${colors.text} mb-3`}>
              {urgencyLabel(urgency)}
            </div>
            <h1 className="text-white text-xl font-bold leading-tight">{offer.hospital_name}</h1>
            <p className="text-white/80 text-sm mb-4">
              {formatRoleLabel(offer.role)}
              {offer.specialty ? ` · ${offer.specialty}` : ''}
            </p>
            <div className="bg-white/15 rounded-xl p-3.5 space-y-2.5">
              <DetailRow icon="📅" label="Shift starts" value={formatShiftDateTime(offer.shift_start)} />
              {offer.shift_end && (
                <DetailRow icon="🏁" label="Ends" value={formatShiftDateTime(offer.shift_end)} />
              )}
              {offer.pay_rate && <DetailRow icon="💰" label="Pay" value={offer.pay_rate} />}
              {offer.notes && <DetailRow icon="📝" label="Note" value={offer.notes} />}
            </div>
          </div>

          <div className="shrink-0 px-5 pt-3 pb-2 border-t border-white/10">
            {offer.shift_start && (
              <p className="text-white/80 text-xs text-center mb-3">
                Accept or decline any time before the shift starts
                {formatShiftDateTime(offer.shift_start) ? ` (${formatShiftDateTime(offer.shift_start)})` : ''}.
              </p>
            )}
            {!canAccept && (
              <p className="text-white/90 text-xs text-center mb-3 bg-white/10 rounded-lg px-3 py-2">
                {acceptBlockedMsg}
              </p>
            )}
            <div className="grid grid-cols-2 gap-3">
              <button
                type="button"
                onClick={handleDecline}
                disabled={responding}
                className="py-3.5 rounded-2xl bg-white/20 text-white font-semibold text-base disabled:opacity-40"
              >
                Decline
              </button>
              <button
                type="button"
                onClick={handleAccept}
                disabled={responding || !canAccept}
                className="py-3.5 rounded-2xl bg-white text-gray-900 font-bold text-base disabled:opacity-60"
              >
                {responding ? 'Accepting…' : canAccept ? 'Accept' : 'Nearby staff only'}
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return createPortal(content, document.body);
}

function DetailRow({ icon, label, value }) {
  return (
    <div className="flex items-start gap-3">
      <span className="text-base leading-none mt-0.5">{icon}</span>
      <div className="flex-1 min-w-0">
        <span className="text-white/60 text-xs uppercase tracking-wide block">{label}</span>
        <span className="text-white text-sm font-medium break-words">{value}</span>
      </div>
    </div>
  );
}
