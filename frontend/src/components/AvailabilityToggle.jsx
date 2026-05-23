/**
 * AvailabilityToggle — nurse availability for the Dashboard.
 * Going online tries GPS first (AvailabilityContext); pincode fallback when GPS fails.
 */
import { Link } from 'react-router-dom';
import { useAvailability } from '../context/AvailabilityContext';
import { useAuth } from '../context/AuthContext';
import { normalizeIndianPincode } from '../utils/geocodePincode';
import NurseActiveShiftSummary from './NurseActiveShiftSummary';

export default function AvailabilityToggle({ activeShift = null, onOpenActiveShift }) {
  const { user } = useAuth();
  const {
    isAvailable,
    loading,
    toggling,
    error,
    toggle,
    isEligible,
    locationSource,
    areaDisplayLabel,
    refreshLocation,
    locRefreshing,
  } = useAvailability();

  if (!isEligible || loading) return null;

  const serverPc = normalizeIndianPincode(user?.service_pincode || '');
  const hasServiceArea = serverPc !== null || Boolean(areaDisplayLabel);

  const areaLine = areaDisplayLabel || '';

  const locLabel = !isAvailable
    ? null
    : locationSource === 'gps'
      ? areaLine
        ? `📍 Live location · ${areaLine}`
        : '📍 Live location'
      : locationSource === 'pincode'
        ? areaLine
          ? `📍 ${areaLine}`
          : '📍 Your saved area'
        : areaLine
          ? `📍 ${areaLine}`
          : '⚠️ Turn on location for best nearby matching';

  const noGpsPing = isAvailable && locationSource === 'none';

  return (
    <div className={`rounded-2xl border p-4 transition-colors ${
      isAvailable ? 'bg-green-50 border-green-200' : 'bg-white border-gray-100 shadow-sm'
    }`}>
      <div className="flex items-center gap-3">

        <div className="shrink-0 relative w-3 h-3">
          <div className={`w-3 h-3 rounded-full ${isAvailable ? 'bg-green-500' : 'bg-gray-300'}`} />
          {isAvailable && (
            <div className="absolute inset-0 rounded-full bg-green-400 animate-ping opacity-60" />
          )}
        </div>

        <div className="flex-1 min-w-0">
          <p className={`text-sm font-semibold leading-tight ${isAvailable ? 'text-green-800' : 'text-gray-700'}`}>
            {isAvailable ? 'Available for Shifts' : 'Offline'}
          </p>
          <p className="text-xs text-gray-400 mt-0.5 leading-tight">
            {isAvailable ? locLabel : 'Go online for urgent nearby shift offers'}
          </p>
        </div>

        <button
          onClick={() => toggle(!isAvailable)}
          disabled={toggling}
          aria-label={isAvailable ? 'Go offline' : 'Go online'}
          className={`shrink-0 px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-colors disabled:opacity-50 ${
            isAvailable
              ? 'bg-white border border-gray-200 text-gray-700 hover:bg-gray-50 active:bg-gray-100'
              : 'bg-green-600 text-white hover:bg-green-700 active:bg-green-800 disabled:bg-gray-300 disabled:text-gray-500 disabled:hover:bg-gray-300'
          }`}
        >
          {toggling ? (
            <span className="flex items-center gap-1.5">
              <span className="w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin" />
              {isAvailable ? 'Going offline' : 'Going online'}
            </span>
          ) : (
            isAvailable ? 'Go Offline' : 'Go Online'
          )}
        </button>
      </div>

      {!hasServiceArea && !isAvailable && (
        <div className="mt-3 rounded-xl bg-amber-50 border border-amber-200 px-3 py-2.5 text-xs text-amber-900 leading-snug space-y-1">
          <p className="font-semibold">Tip: go online with GPS for best nearby matching</p>
          <p className="text-amber-800">
            Tap Go Online to use your current location, or save a pincode in Profile as backup.
          </p>
          <Link to="/profile" className="inline-block mt-1 text-indigo-700 font-semibold underline">
            Set service area in Profile →
          </Link>
        </div>
      )}

      {isAvailable && (
        <button
          type="button"
          onClick={refreshLocation}
          disabled={locRefreshing || toggling}
          className="mt-3 w-full min-h-[40px] rounded-xl border border-green-200 bg-white text-green-800 text-xs font-semibold disabled:opacity-50"
        >
          {locRefreshing ? 'Updating your area…' : '📍 Update current area'}
        </button>
      )}

      {noGpsPing && (
        <div className="mt-2 text-xs text-amber-700 bg-white/70 rounded-lg px-2 py-1.5">
          GPS unavailable this session — if offers are missed, enable location permission and try again.
        </div>
      )}

      {error && (
        <p className="text-xs text-red-600 mt-2 bg-red-50 px-3 py-1.5 rounded-lg">{error}</p>
      )}

      <NurseActiveShiftSummary
        shift={activeShift}
        onOpen={() => activeShift && onOpenActiveShift?.(activeShift)}
      />
    </div>
  );
}
