/**
 * DispatchOfferModal — bottom-sheet dispatch offer for nurses.
 *
 * Appears as a bottom sheet when a dispatch_offer WebSocket message arrives.
 * The top ~15% of the screen stays visible so the nurse can still access
 * the header (logo, navigation) while the offer is active.
 * The bottom nav remains interactive above the sheet (z-50 > sheet z-40).
 *
 * Layout:
 *   • Drag handle + dismiss button at top
 *   • Scrollable shift details in the middle
 *   • Countdown + Accept/Decline always pinned at the bottom (never scrolls away)
 *   • Bottom padding accounts for nav bar (4rem) + Android safe-area-inset-bottom
 *
 * Accept → POST /dispatch/offers/{offer_id}/accept
 * Decline → POST /dispatch/offers/{offer_id}/decline
 * Dismiss (×) → closes sheet WITHOUT declining; offer stays pending in backend
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import api from '../api/axios';

const URGENCY_COLORS = {
  emergency: { bg: 'bg-red-600',   ring: 'ring-red-400',   text: 'text-red-100',  badge: 'bg-red-800'  },
  urgent:    { bg: 'bg-orange-500', ring: 'ring-orange-300', text: 'text-orange-50', badge: 'bg-orange-700' },
  standard:  { bg: 'bg-indigo-600', ring: 'ring-indigo-400', text: 'text-indigo-100', badge: 'bg-indigo-800' },
  planned:   { bg: 'bg-teal-600',   ring: 'ring-teal-400',   text: 'text-teal-100', badge: 'bg-teal-800' },
};

const URGENCY_LABELS = {
  emergency: '🚨 EMERGENCY',
  urgent:    '⚡ URGENT',
  standard:  '📋 Standard',
  planned:   '📅 Planned',
};

function formatRole(role) {
  return role?.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) || '';
}

function formatTime(isoString) {
  if (!isoString) return '';
  const d = new Date(isoString);
  return d.toLocaleString('en-IN', {
    weekday: 'short', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: true,
  });
}

export default function DispatchOfferModal({ offer, onClose }) {
  // Compute true remaining seconds (accounts for time elapsed since offer was received)
  const trueInitial = Math.max(1, (offer?.expires_in_sec || 30) - Math.floor((Date.now() - (offer?._receivedAt || Date.now())) / 1000));
  const [secondsLeft, setSecondsLeft] = useState(trueInitial);
  const [responding, setResponding] = useState(false);
  const [result, setResult] = useState(null); // 'accepted' | 'declined' | 'error' | 'expired'
  const [errorMsg, setErrorMsg] = useState('');
  const timerRef = useRef(null);
  const resultRef = useRef(null);

  // Countdown timer — resets on each new offer
  useEffect(() => {
    if (!offer) return;
    const initialSec = Math.max(1, (offer.expires_in_sec || 30) - Math.floor((Date.now() - (offer._receivedAt || Date.now())) / 1000));
    setSecondsLeft(initialSec);
    resultRef.current = null;

    timerRef.current = setInterval(() => {
      setSecondsLeft(prev => {
        if (prev <= 1) {
          clearInterval(timerRef.current);
          if (!resultRef.current) {
            resultRef.current = 'expired';
            setResult('expired');
          }
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    return () => clearInterval(timerRef.current);
  }, [offer?.offer_id]);

  // Auto-close expired screen
  useEffect(() => {
    if (result !== 'expired') return;
    const t = setTimeout(onClose, 2500);
    return () => clearTimeout(t);
  }, [result, onClose]);

  const handleAccept = useCallback(async () => {
    if (responding || result) return;
    resultRef.current = 'accepting';
    setResponding(true);
    try {
      await api.post(`/dispatch/offers/${offer.offer_id}/accept`);
      clearInterval(timerRef.current);
      resultRef.current = 'accepted';
      setResult('accepted');
      setTimeout(onClose, 2500);
    } catch (err) {
      const detail = err.response?.data?.detail;
      const msg = typeof detail === 'string' ? detail : 'Failed to accept. Please try again.';
      setErrorMsg(msg);
      resultRef.current = 'error';
      setResult('error');
      setTimeout(onClose, 3500);
    } finally {
      setResponding(false);
    }
  }, [offer?.offer_id, responding, result, onClose]);

  const handleDecline = useCallback(async () => {
    if (responding || result) return;
    resultRef.current = 'declined';
    setResponding(true);
    clearInterval(timerRef.current);
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

  if (!offer) return null;

  const urgency = offer.urgency || 'standard';
  const colors = URGENCY_COLORS[urgency] || URGENCY_COLORS.standard;
  const timerPct = Math.max(0, (secondsLeft / (offer.expires_in_sec || 30)) * 100);
  const timerColor = timerPct > 50 ? 'bg-green-400' : timerPct > 20 ? 'bg-yellow-400' : 'bg-red-400';

  // ── Result screens — full-screen, brief feedback, z-50 ──────────────────────
  if (result === 'accepted') {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-green-700"
        style={{ paddingTop: 'env(safe-area-inset-top, 0px)', paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}>
        <div className="text-center text-white px-6 max-w-sm">
          <div className="w-16 h-16 rounded-full bg-white/20 flex items-center justify-center mx-auto mb-5">
            <svg className="w-9 h-9 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <h2 className="text-2xl font-bold mb-1">Assignment Confirmed</h2>
          <p className="text-green-100 text-base mt-1">{offer.hospital_name}</p>
          <p className="text-green-200 text-sm mt-1">{formatTime(offer.shift_start)}</p>
          <p className="text-green-300 text-xs mt-4">Please arrive on time. Good luck!</p>
        </div>
      </div>
    );
  }

  if (result === 'declined') {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-700"
        style={{ paddingTop: 'env(safe-area-inset-top, 0px)', paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}>
        <div className="text-center text-white px-6">
          <div className="text-4xl mb-3 opacity-80">—</div>
          <h2 className="text-lg font-semibold text-gray-200">Offer Declined</h2>
        </div>
      </div>
    );
  }

  if (result === 'expired') {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-800"
        style={{ paddingTop: 'env(safe-area-inset-top, 0px)', paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}>
        <div className="text-center text-white px-6">
          <div className="w-14 h-14 rounded-full bg-white/10 flex items-center justify-center mx-auto mb-4">
            <svg className="w-7 h-7 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <h2 className="text-lg font-semibold text-gray-200">Offer Expired</h2>
          <p className="text-gray-400 text-sm mt-1">The response window closed.</p>
        </div>
      </div>
    );
  }

  if (result === 'error') {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-800"
        style={{ paddingTop: 'env(safe-area-inset-top, 0px)', paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}>
        <div className="text-center text-white px-6 max-w-sm">
          <div className="w-14 h-14 rounded-full bg-red-500/20 flex items-center justify-center mx-auto mb-4">
            <svg className="w-7 h-7 text-red-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <h2 className="text-lg font-semibold mb-2">Unable to Accept</h2>
          <p className="text-gray-300 text-sm">{errorMsg}</p>
          <p className="text-gray-500 text-xs mt-3">This window will close automatically.</p>
        </div>
      </div>
    );
  }

  // ── Active offer — bottom sheet (z-40, below header z-50 and nav z-50) ────────
  // The top header (logo + nav) remains visible and tappable above the sheet.
  // The bottom nav remains visible and tappable on top of the sheet.
  // Bottom padding = nav height (4rem) + Android safe-area-inset-bottom.
  return (
    <>
      {/* Dim backdrop — pointer-events-none so header and background are still tappable */}
      <div className="fixed inset-0 z-30 bg-black/40 pointer-events-none" />

      {/* Bottom sheet */}
      <div
        className={`fixed bottom-0 inset-x-0 z-40 rounded-t-2xl flex flex-col shadow-2xl overflow-hidden ${colors.bg}`}
        style={{
          maxHeight: '88vh',
          paddingBottom: 'calc(4rem + env(safe-area-inset-bottom, 0px))',
        }}
      >
        {/* Drag handle + dismiss row */}
        <div className="flex items-center justify-between px-4 pt-2.5 pb-1 shrink-0">
          <div className="w-6" /> {/* spacer */}
          <div className="w-10 h-1 bg-white/30 rounded-full" />
          <button
            onClick={onClose}
            className="w-6 h-6 flex items-center justify-center text-white/60 hover:text-white active:scale-90 transition-transform"
            title="Dismiss — offer stays pending"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Timer progress bar */}
        <div className="h-1.5 bg-black/20 w-full shrink-0">
          <div
            className={`h-full transition-all duration-1000 ${timerColor}`}
            style={{ width: `${timerPct}%` }}
          />
        </div>

        {/* Scrollable shift details */}
        <div className="flex-1 overflow-y-auto px-5 pt-4 pb-3">
          {/* Urgency badge */}
          <div className={`inline-block text-xs font-bold px-3 py-1 rounded-full ${colors.badge} ${colors.text} mb-3 tracking-wide`}>
            {URGENCY_LABELS[urgency]}
          </div>

          <h1 className="text-white text-xl font-bold leading-tight mb-0.5">
            {offer.hospital_name}
          </h1>
          <p className="text-white/80 text-sm mb-4">
            {formatRole(offer.role)}
            {offer.specialty ? ` · ${offer.specialty}` : ''}
          </p>

          <div className="bg-white/15 rounded-xl p-3.5 space-y-2.5">
            <DetailRow icon="📅" label="Shift starts" value={formatTime(offer.shift_start)} />
            {offer.shift_end && (
              <DetailRow icon="🏁" label="Ends" value={formatTime(offer.shift_end)} />
            )}
            {offer.city_id && (
              <DetailRow icon="📍" label="City" value={offer.city_id} />
            )}
            {offer.pay_rate && (
              <DetailRow icon="💰" label="Pay" value={offer.pay_rate} />
            )}
            {offer.notes && (
              <DetailRow icon="📝" label="Note" value={offer.notes} />
            )}
            <DetailRow icon="🌊" label="Wave" value={`Wave ${offer.wave || 1}`} />
          </div>
        </div>

        {/* Pinned CTA — never scrolls out of view */}
        <div className="shrink-0 px-5 pt-3 pb-3 border-t border-white/10">
          {/* Countdown */}
          <div className="flex items-center justify-center gap-3 mb-3">
            <span className={`text-4xl font-mono font-bold tabular-nums leading-none ${
              secondsLeft <= 10 ? 'text-red-200' : 'text-white'
            } ${secondsLeft <= 5 ? 'animate-pulse' : ''}`}>
              {secondsLeft}
            </span>
            <span className="text-white/60 text-xs uppercase tracking-widest leading-tight">
              seconds<br />to respond
            </span>
          </div>

          {/* Action buttons */}
          <div className="grid grid-cols-2 gap-3">
            <button
              onClick={handleDecline}
              disabled={responding}
              className="py-3.5 rounded-2xl bg-white/20 text-white font-semibold text-base active:scale-95 transition-transform disabled:opacity-40"
            >
              Decline
            </button>
            <button
              onClick={handleAccept}
              disabled={responding}
              className="py-3.5 rounded-2xl bg-white text-gray-900 font-bold text-base shadow-lg active:scale-95 transition-transform disabled:opacity-60"
            >
              {responding ? (
                <span className="flex items-center justify-center gap-2">
                  <span className="w-4 h-4 border-2 border-gray-400 border-t-transparent rounded-full animate-spin" />
                  Accepting
                </span>
              ) : (
                <span className="flex items-center justify-center gap-1.5">
                  Accept
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                  </svg>
                </span>
              )}
            </button>
          </div>
        </div>
      </div>
    </>
  );
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
