import { useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { DISPATCH_ELIGIBLE_ROLES } from '../context/AvailabilityContext';
import api from '../api/axios';
import {
  reverseGeocodeCoords,
  normalizeIndianPincode,
  savePincode,
  saveLastKnownArea,
} from '../utils/geocodePincode';
import { mlog } from '../utils/mobileLogger';

const STORAGE_KEY = 'mr_loc_edu_v1';

/**
 * One-time explainer before location permission (nurses / dispatch-eligible staff).
 */
export default function LocationEducationModal() {
  const { user } = useAuth();
  const eligible = user?.role && DISPATCH_ELIGIBLE_ROLES.has(user.role);
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

  if (!open || !eligible) return null;

  function dismiss() {
    try {
      localStorage.setItem(STORAGE_KEY, '1');
    } catch { /* ignore */ }
    setOpen(false);
  }

  async function enableLocation() {
    if (!navigator.geolocation) {
      setErr('Location is not supported on this device. You can set your area in Profile.');
      return;
    }
    setBusy(true);
    setErr('');
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        try {
          const rev = await reverseGeocodeCoords(pos.coords.latitude, pos.coords.longitude);
          const pc = normalizeIndianPincode(rev.pincode);
          if (pc) {
            savePincode(pc);
            saveLastKnownArea({
              locality: rev.locality,
              pincode: pc,
              lat: pos.coords.latitude,
              lng: pos.coords.longitude,
            });
            await api.put('/profile/me', {
              service_pincode: pc,
              service_locality: rev.locality || null,
              location_source: 'gps',
            }).catch(() => {});
            mlog('location', 'edu_gps_saved', {});
          } else {
            setErr('Could not detect your pincode. Set your area manually in Profile.');
          }
        } catch {
          setErr('Area lookup failed. Try again from Profile.');
        } finally {
          setBusy(false);
          dismiss();
        }
      },
      () => {
        setErr('Location denied. You can enter your pincode in Profile anytime.');
        setBusy(false);
        mlog('location', 'edu_gps_denied', {});
      },
      { timeout: 15000, maximumAge: 0, enableHighAccuracy: true },
    );
  }

  return (
    <div className="fixed inset-0 z-[90] flex items-end sm:items-center justify-center p-4 bg-black/45">
      <div
        className="w-full max-w-md bg-white rounded-2xl shadow-xl p-5"
        role="dialog"
        aria-labelledby="loc-edu-title"
      >
        <h2 id="loc-edu-title" className="text-lg font-bold text-gray-900">
          Find shifts near you
        </h2>
        <p className="text-sm text-gray-600 mt-2 leading-relaxed">
          Like ride-hailing and delivery apps, MediRoute uses your location to show nearby hospital shifts
          in areas such as Madhapur, Kukatpally, or Banjara Hills — not generic city codes.
        </p>
        <ul className="mt-3 text-sm text-gray-700 space-y-1.5 list-disc pl-5">
          <li>Live GPS gives the most accurate nearby matching</li>
          <li>If GPS is off, your saved pincode is used instead</li>
          <li>You can update your area anytime in Profile</li>
        </ul>
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
