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
  const [result, setResult] = useState(null); // 'accepted' | 'declined' | 'error'
  const [errorMsg, setErrorMsg] = useState('');
  const timerRef = useRef(null);

  // Countdown timer
  useEffect(() => {
    if (!offer) return;
    const initialSec = offer.expires_in_sec || 30;
    setSecondsLeft(initialSec);

    timerRef.current = setInterval(() => {
      setSecondsLeft(prev => {
        if (prev <= 1) {
          clearInterval(timerRef.current);
          if (!result) setResult('expired');
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    return () => clearInterval(timerRef.current);
  }, [offer?.offer_id]);

  const handleAccept = useCallback(async () => {
    if (responding || result) return;
    setResponding(true);
    try {
      await api.post(`/dispatch/offers/${offer.offer_id}/accept`);
      clearInterval(timerRef.current);
      setResult('accepted');
      setTimeout(onClose, 2500);
    } catch (err) {
      const msg = err.response?.data?.detail || 'Failed to accept. Please try again.';
      setErrorMsg(msg);
      setResult('error');
      setTimeout(onClose, 3000);
    } finally {
      setResponding(false);
    }
  }, [offer?.offer_id, responding, result, onClose]);

  const handleDecline = useCallback(async () => {
    if (responding || result) return;
    setResponding(true);
    try {
      await api.post(`/dispatch/offers/${offer.offer_id}/decline`);
      clearInterval(timerRef.current);
      setResult('declined');
      setTimeout(onClose, 1500);
    } catch {
      setResult('declined');
      setTimeout(onClose, 1500);
    } finally {
      setResponding(false);
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
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-green-600">
        <div className="text-center text-white px-6">
          <div className="text-6xl mb-4">✅</div>
          <h2 className="text-2xl font-bold mb-2">Assignment Confirmed!</h2>
          <p className="text-green-100">{offer.hospital_name}</p>
          <p className="text-green-200 text-sm mt-1">{formatTime(offer.shift_start)}</p>
        </div>
      </div>
    );
  }

  if (result === 'declined') {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-700">
        <div className="text-center text-white px-6">
          <div className="text-5xl mb-4">👋</div>
          <h2 className="text-xl font-bold">Offer Declined</h2>
        </div>
      </div>
    );
  }

  if (result === 'expired') {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-800">
        <div className="text-center text-white px-6">
          <div className="text-5xl mb-4">⏰</div>
          <h2 className="text-xl font-bold">Offer Expired</h2>
          <p className="text-gray-300 text-sm mt-2">The offer window closed.</p>
        </div>
      </div>
    );
  }

  if (result === 'error') {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-800">
        <div className="text-center text-white px-6 max-w-sm">
          <div className="text-5xl mb-4">⚠️</div>
          <h2 className="text-xl font-bold mb-2">Unable to Accept</h2>
          <p className="text-gray-300 text-sm">{errorMsg}</p>
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
            <DetailRow icon="📅" label="Shift" value={formatTime(offer.shift_start)} />
            {offer.shift_end && (
              <DetailRow icon="🏁" label="Until" value={formatTime(offer.shift_end)} />
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
            <span className={`text-5xl font-mono font-bold ${secondsLeft <= 10 ? 'text-red-200 animate-pulse' : 'text-white'}`}>
              {secondsLeft}
            </span>
            <p className="text-white/60 text-xs mt-1 uppercase tracking-widest">seconds to respond</p>
          </div>

          {/* Action buttons */}
          <div className="grid grid-cols-2 gap-3">
            <button
              onClick={handleDecline}
              disabled={responding}
              className="py-4 rounded-2xl bg-white/20 text-white font-semibold text-base active:scale-95 transition-transform disabled:opacity-50"
            >
              Decline
            </button>
            <button
              onClick={handleAccept}
              disabled={responding}
              className={`py-4 rounded-2xl bg-white text-gray-900 font-bold text-base shadow-lg active:scale-95 transition-transform disabled:opacity-50 ${
                responding ? 'opacity-70' : ''
              }`}
            >
              {responding ? '...' : 'Accept ✓'}
            </button>
          </div>

          <p className="text-white/40 text-xs text-center mt-4">
            Accepting confirms you'll arrive on time
          </p>
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
