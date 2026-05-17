/**
 * AvailabilityContext — global nurse availability state.
 *
 * Lives at app root so the heartbeat runs on any page while the nurse is
 * marked available. Mounting only in Dashboard would stop the heartbeat
 * whenever the nurse navigates away, causing stale-out after 5 minutes.
 *
 * Only active for dispatch-eligible roles. No-op for recruiters/admins.
 *
 * API surface:
 *   GET  /availability/status   — initial state on mount
 *   PUT  /availability/toggle   — go available / go offline
 *   PUT  /availability/location — heartbeat every 90s while available
 */
import {
  createContext,
  useContext,
  useState,
  useEffect,
  useRef,
  useCallback,
} from 'react';
import api from '../api/axios';

const HEARTBEAT_INTERVAL_MS = 90_000;

// Must mirror DISPATCH_ELIGIBLE_ROLES in backend availability.py
const DISPATCH_ELIGIBLE_ROLES = new Set([
  'nurse', 'staff_nurse', 'icu_nurse', 'ot_nurse', 'emergency_nurse',
  'home_care_nurse', 'doctor', 'lab_tech', 'pharmacist', 'driver', 'front_office',
]);

const AvailabilityContext = createContext(null);

function getGeoPosition() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error('no-geolocation'));
      return;
    }
    navigator.geolocation.getCurrentPosition(resolve, reject, {
      timeout: 8000,
      maximumAge: 60_000,
      enableHighAccuracy: false, // saves battery; good enough for city-level dispatch
    });
  });
}

export function AvailabilityProvider({ children, user }) {
  const isEligible = DISPATCH_ELIGIBLE_ROLES.has(user?.role);

  // State
  const [isAvailable, setIsAvailable]     = useState(false);
  const [presenceState, setPresenceState] = useState('offline');
  const [cityId, setCityId]               = useState('HYD');
  const [loading, setLoading]             = useState(isEligible); // hide toggle until state loads
  const [toggling, setToggling]           = useState(false);
  const [error, setError]                 = useState('');

  // Persist last known coordinates for heartbeat
  const latRef  = useRef(null);
  const lngRef  = useRef(null);
  const cityRef = useRef('HYD');
  const heartbeatRef = useRef(null);

  // ── Load initial state on mount (once per login) ──────────────────────────
  useEffect(() => {
    if (!isEligible || !user?.id) {
      setLoading(false);
      return;
    }
    api.get('/availability/status')
      .then(res => {
        setIsAvailable(res.data.is_available);
        setPresenceState(res.data.presence_state);
        const city = res.data.city_id || 'HYD';
        setCityId(city);
        cityRef.current = city;
        if (res.data.latitude != null) {
          latRef.current = res.data.latitude;
          lngRef.current = res.data.longitude;
        }
      })
      .catch(() => {}) // non-critical — default to offline state
      .finally(() => setLoading(false));
  }, [user?.id, isEligible]);

  // ── Heartbeat: keep last_seen fresh while nurse is available ──────────────
  // Backend marks nurses with last_seen > 5 min as stale for dispatch.
  // 90s interval gives 3 refreshes within the 5-min window.
  useEffect(() => {
    clearInterval(heartbeatRef.current);
    if (!isAvailable || !isEligible) return;

    const sendHeartbeat = () => {
      if (latRef.current == null) return; // no coords yet — skip
      api.put('/availability/location', {
        latitude:  latRef.current,
        longitude: lngRef.current,
        city_id:   cityRef.current,
      }).catch(() => {}); // heartbeat failure is non-critical
    };

    heartbeatRef.current = setInterval(sendHeartbeat, HEARTBEAT_INTERVAL_MS);
    return () => clearInterval(heartbeatRef.current);
  }, [isAvailable, isEligible]);

  // Cleanup on unmount
  useEffect(() => () => clearInterval(heartbeatRef.current), []);

  // ── Toggle availability ───────────────────────────────────────────────────
  const toggle = useCallback(async (wantAvailable) => {
    if (toggling || !isEligible) return;
    setToggling(true);
    setError('');

    let lat = null;
    let lng = null;

    if (wantAvailable) {
      // Request geolocation — needed for nurse-to-hospital distance matching.
      // If denied/unavailable, we still toggle (backend accepts null coords).
      try {
        const pos = await getGeoPosition();
        lat = pos.coords.latitude;
        lng = pos.coords.longitude;
        latRef.current = lat;
        lngRef.current = lng;
      } catch {
        // Permission denied or not supported — proceed without coords.
        // Backend will exclude this nurse from geo-filtered dispatches.
      }
    }

    try {
      const res = await api.put('/availability/toggle', {
        is_available: wantAvailable,
        latitude:     lat,
        longitude:    lng,
        city_id:      cityRef.current,
      });
      setIsAvailable(res.data.is_available);
      setPresenceState(res.data.presence_state);
    } catch (err) {
      const detail = err.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : 'Could not update availability. Please try again.');
    } finally {
      setToggling(false);
    }
  }, [toggling, isEligible]);

  const value = {
    isAvailable,
    presenceState,
    cityId,
    loading,
    toggling,
    error,
    toggle,
    isEligible,
  };

  return (
    <AvailabilityContext.Provider value={value}>
      {children}
    </AvailabilityContext.Provider>
  );
}

export function useAvailability() {
  const ctx = useContext(AvailabilityContext);
  if (!ctx) throw new Error('useAvailability must be used within AvailabilityProvider');
  return ctx;
}
