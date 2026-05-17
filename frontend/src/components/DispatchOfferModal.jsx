/**
 * DispatchOfferModal — full-screen dispatch offer for nurses.
 *
 * Appears immediately when a dispatch_offer WebSocket message arrives.
 * Occupies the full screen (z-50) so the nurse cannot miss it.
 * Shows a countdown timer with urgency-appropriate colors.
 * Accept → POST /dispatch/offers/{offer_id}/accept
 * Decline → POST /dispatch/offers/{offer_id}/decline
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
  const [secondsLeft, setSecondsLeft] = useState(offer?.expires_in_sec || 30);
  const [responding, setResponding] = useState(false);
  const [result, setResult] = useState(null); // 'accepted' | 'declined' | 'error' | 'expired'
  const [errorMsg, setErrorMsg] = useState('');
  const timerRef = useRef(null);
  const resultRef = useRef(null); // tracks result without stale closure

  // Countdown timer — resets on each new offer
  useEffect(() => {
    if (!offer) return;
    const initialSec = offer.expires_in_sec || 30;
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

  // Auto-close result screens — expired/declined close quickly; accepted/error are handled
  // in their action handlers with appropriate delays.
  useEffect(() => {
    if (result !== 'expired') return;
    const t = setTimeout(onClose, 2500);
    return () => clearTimeout(t);
  }, [result, onClose]);

  const handleAccept = useCallback(async () => {
    if (responding || result) return;
    resultRef.current = 'accepting'; // prevents timer from triggering 'expired'
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
      // best-effort — always mark declined locally
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

  // Result screens
  if (result === 'accepted') {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-green-700">
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
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-700">
        <div className="text-center text-white px-6">
          <div className="text-4xl mb-3 opacity-80">—</div>
          <h2 className="text-lg font-semibold text-gray-200">Offer Declined</h2>
        </div>
      </div>
    );
  }

  if (result === 'expired') {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-800">
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
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-800">
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

  return (
    <div className={`fixed inset-0 z-50 flex flex-col ${colors.bg} overflow-hidden`}>
      {/* Timer bar */}
      <div className="h-1.5 bg-black/20 w-full">
        <div
          className={`h-full transition-all duration-1000 ${timerColor}`}
          style={{ width: `${timerPct}%` }}
        />
      </div>

      {/* Content */}
      <div className="flex-1 flex flex-col justify-between px-6 py-8 overflow-y-auto">
        {/* Header */}
        <div>
          <div className={`inline-block text-xs font-bold px-3 py-1 rounded-full ${colors.badge} ${colors.text} mb-4 tracking-wide`}>
            {URGENCY_LABELS[urgency]}
          </div>

          <h1 className="text-white text-2xl font-bold leading-tight mb-1">
            {offer.hospital_name}
          </h1>
          <p className="text-white/80 text-base mb-6">
            {formatRole(offer.role)}
            {offer.specialty ? ` · ${offer.specialty}` : ''}
          </p>

          {/* Shift details */}
          <div className="bg-white/15 rounded-2xl p-4 space-y-3 mb-6">
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

        {/* Timer + CTA */}
        <div>
          {/* Countdown */}
          <div className="text-center mb-6">
            <span className={`text-5xl font-mono font-bold tabular-nums ${
              secondsLeft <= 10 ? 'text-red-200' : 'text-white'
            } ${secondsLeft <= 5 ? 'animate-pulse' : ''}`}>
              {secondsLeft}
            </span>
            <p className="text-white/60 text-xs mt-1 uppercase tracking-widest">seconds to respond</p>
          </div>

          {/* Action buttons */}
          <div className="grid grid-cols-2 gap-3">
            <button
              onClick={handleDecline}
              disabled={responding}
              className="py-4 rounded-2xl bg-white/20 text-white font-semibold text-base active:scale-95 transition-transform disabled:opacity-40"
            >
              Decline
            </button>
            <button
              onClick={handleAccept}
              disabled={responding}
              className="py-4 rounded-2xl bg-white text-gray-900 font-bold text-base shadow-lg active:scale-95 transition-transform disabled:opacity-60"
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
    </div>
  );
}

function DetailRow({ icon, label, value }) {
  return (
    <div className="flex items-start gap-3">
      <span className="text-lg leading-none mt-0.5">{icon}</span>
      <div className="flex-1 min-w-0">
        <span className="text-white/60 text-xs uppercase tracking-wide block">{label}</span>
        <span className="text-white text-sm font-medium break-words">{value}</span>
      </div>
    </div>
  );
}
