import { useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { DISPATCH_ELIGIBLE_ROLES } from '../context/AvailabilityContext';
import {
  captureCurrentArea,
  openAppSettings,
  LOCATION_AUDIENCE,
} from '../utils/deviceLocation';
import { mlog } from '../utils/mobileLogger';

const STORAGE_KEY = 'mr_loc_edu_v1';

export default function LocationEducationModal() {
  const { user, refreshUser } = useAuth();
  const eligible = user?.role && DISPATCH_ELIGIBLE_ROLES.has(user.role);
  const copy = LOCATION_AUDIENCE.job_seeker;
  const [open, setOpen] = useState(() => {
    if (!eligible) return false;
    try {
      return localStorage.getItem(STORAGE_KEY) !== '1';
    } catch {
      return true;
    }
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [showSettings, setShowSettings] = useState(false);

  if (!open || !eligible) return null;

  function dismiss() {
    try {
      localStorage.setItem(STORAGE_KEY, '1');
    } catch { /* ignore */ }
    setOpen(false);
  }

  async function enableLocation() {
    setBusy(true);
    setErr('');
    setShowSettings(false);
    const cap = await captureCurrentArea({
      audience: 'job_seeker',
      highAccuracy: true,
      syncProfile: true,
    });
    if (cap.ok) {
      mlog('location', 'edu_gps_saved', { locality: cap.locality?.slice(0, 20) });
      await refreshUser?.().catch(() => {});
      dismiss();
    } else {
      setErr(cap.userMessage || copy.denied);
      setShowSettings(cap.permissionState === 'permanent');
      mlog('location', 'edu_gps_fail', { state: cap.permissionState });
    }
    setBusy(false);
  }

  return (
    <div className="fixed inset-0 z-[90] flex items-end sm:items-center justify-center p-4 bg-black/45">
      <div
        className="w-full max-w-md bg-white rounded-2xl shadow-xl p-5"
        role="dialog"
        aria-labelledby="loc-edu-title"
      >
        <h2 id="loc-edu-title" className="text-lg font-bold text-gray-900">
          {copy.permissionTitle}
        </h2>
        <p className="text-sm text-gray-600 mt-2 leading-relaxed">{copy.permissionBody}</p>
        <p className="text-sm text-gray-600 mt-2 leading-relaxed">
          We show real area names like Madhapur, Kukatpally, or Banjara Hills — not generic city codes.
        </p>
        {err && (
          <p className="mt-3 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
            {err}
          </p>
        )}
        <div className="mt-5 flex flex-col gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={enableLocation}
            className="min-h-[48px] rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white font-semibold text-sm disabled:opacity-50"
          >
            {busy ? 'Detecting your area…' : 'Use my current location'}
          </button>
          {showSettings && (
            <button
              type="button"
              onClick={() => openAppSettings()}
              className="min-h-[44px] rounded-xl border border-indigo-200 text-indigo-700 font-semibold text-sm"
            >
              Open app settings
            </button>
          )}
          {!showSettings && err && (
            <button
              type="button"
              disabled={busy}
              onClick={enableLocation}
              className="min-h-[44px] rounded-xl border border-gray-200 text-gray-700 font-medium text-sm"
            >
              Try again
            </button>
          )}
          <button
            type="button"
            onClick={dismiss}
            className="min-h-[44px] rounded-xl border border-gray-200 text-gray-700 font-medium text-sm"
          >
            Not now — set pincode in Profile
          </button>
        </div>
      </div>
    </div>
  );
}
