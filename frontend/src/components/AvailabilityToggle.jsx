/**
 * AvailabilityToggle — nurse availability toggle for the Dashboard.
 *
 * Shows current dispatch availability state and lets the nurse go online/offline
 * with a single tap. Requests geolocation when going available (needed for
 * nurse-to-hospital distance matching in the dispatch engine).
 *
 * Design principles:
 *   - Operationally clear: state is unambiguous at a glance
 *   - Heartbeat is managed globally in AvailabilityContext — not here
 *   - No flashy animations — the pulsing green dot is a real-time alive signal
 */
import { useAvailability } from '../context/AvailabilityContext';

export default function AvailabilityToggle() {
  const { isAvailable, cityId, loading, toggling, error, toggle, isEligible } = useAvailability();

  // Only show for dispatch-eligible roles; never show during initial load
  if (!isEligible || loading) return null;

  return (
    <div className={`rounded-2xl border p-4 transition-colors ${
      isAvailable
        ? 'bg-green-50 border-green-200'
        : 'bg-white border-gray-100 shadow-sm'
    }`}>
      <div className="flex items-center gap-3">

        {/* Availability indicator dot */}
        <div className="shrink-0 relative w-3 h-3">
          <div className={`w-3 h-3 rounded-full ${
            isAvailable ? 'bg-green-500' : 'bg-gray-300'
          }`} />
          {/* Pulsing ring — only when actively available (real-time signal) */}
          {isAvailable && (
            <div className="absolute inset-0 rounded-full bg-green-400 animate-ping opacity-60" />
          )}
        </div>

        {/* Status text */}
        <div className="flex-1 min-w-0">
          <p className={`text-sm font-semibold leading-tight ${
            isAvailable ? 'text-green-800' : 'text-gray-700'
          }`}>
            {isAvailable ? 'Available for Shifts' : 'Offline'}
          </p>
          <p className="text-xs text-gray-400 mt-0.5 leading-tight">
            {isAvailable
              ? `Receiving dispatch offers · ${cityId}`
              : 'Go online to receive urgent shift offers'}
          </p>
        </div>

        {/* Toggle button */}
        <button
          onClick={() => toggle(!isAvailable)}
          disabled={toggling}
          aria-label={isAvailable ? 'Go offline' : 'Go online'}
          className={`shrink-0 px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-colors disabled:opacity-50 ${
            isAvailable
              ? 'bg-white border border-gray-200 text-gray-700 hover:bg-gray-50 active:bg-gray-100'
              : 'bg-green-600 text-white hover:bg-green-700 active:bg-green-800'
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

      {/* Error message — only shown on toggle failure */}
      {error && (
        <p className="text-xs text-red-600 mt-2 bg-red-50 px-3 py-1.5 rounded-lg">
          {error}
        </p>
      )}
    </div>
  );
}
