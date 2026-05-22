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
import {
  geocodePincode,
  loadPincode,
  reverseGeocodeCoords,
  normalizeIndianPincode,
  savePincode,
  saveLastKnownArea,
  loadLastKnownArea,
} from '../utils/geocodePincode';
import { humanizeCityId } from '../utils/areaLabel';
import { mlog, mlogError } from '../utils/mobileLogger';

const HEARTBEAT_INTERVAL_MS = 90_000;

// Must mirror DISPATCH_ELIGIBLE_ROLES in backend availability.py
export const DISPATCH_ELIGIBLE_ROLES = new Set([
  'nurse', 'staff_nurse', 'icu_nurse', 'ot_nurse', 'emergency_nurse',
  'home_care_nurse', 'doctor', 'lab_tech', 'pharmacist', 'driver', 'front_office',
]);

const AvailabilityContext = createContext(null);

function getGeoPosition({ highAccuracy = true, maxAge = 45_000 } = {}) {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error('no-geolocation'));
      return;
    }
    navigator.geolocation.getCurrentPosition(resolve, reject, {
      timeout: 12_000,
      maximumAge: maxAge,
      enableHighAccuracy: highAccuracy,
    });
  });
}

async function syncGpsToProfile(lat, lng) {
  try {
    const rev = await reverseGeocodeCoords(lat, lng);
    const pc = normalizeIndianPincode(rev.pincode);
    if (rev.locality || pc) {
      saveLastKnownArea({ locality: rev.locality, pincode: pc, lat, lng });
    }
    if (pc) savePincode(pc);
    if (pc || rev.locality) {
      await api.put('/profile/me', {
        ...(pc ? { service_pincode: pc } : {}),
        ...(rev.locality ? { service_locality: rev.locality } : {}),
        location_source: 'gps',
      });
      mlog('location', 'profile_gps_sync_ok', { has_pin: Boolean(pc) });
    }
    return rev;
  } catch (e) {
    mlog('location', 'profile_gps_sync_fail', { err: e?.message });
    return null;
  }
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
  // 'gps' | 'pincode' | 'none' — which location source was used when going online
  const [locationSource, setLocationSource] = useState('none');
  const [sessionAreaLabel, setSessionAreaLabel] = useState('');

  // Persist last known coordinates for heartbeat
  const latRef  = useRef(null);
  const lngRef  = useRef(null);
  const cityRef = useRef('HYD');
  const heartbeatRef = useRef(null);
  const heartbeatCountRef = useRef(0);

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

    const last = loadLastKnownArea();
    if (last?.locality) setSessionAreaLabel(last.locality);
    else if (user?.service_locality) setSessionAreaLabel(user.service_locality);
  }, [user?.id, isEligible, user?.service_locality]);

  // ── Heartbeat: keep last_seen fresh while nurse is available ──────────────
  // Backend marks nurses with last_seen > 5 min as stale for dispatch.
  // 90s interval gives 3 refreshes within the 5-min window.
  useEffect(() => {
    clearInterval(heartbeatRef.current);
    if (!isAvailable || !isEligible) return;

    const sendHeartbeat = async () => {
      heartbeatCountRef.current += 1;
      // Refresh GPS every 3rd heartbeat (~4.5 min) while online — stable, not flickering
      if (heartbeatCountRef.current % 3 === 0) {
        try {
          const pos = await getGeoPosition({ highAccuracy: false, maxAge: 120_000 });
          const newLat = pos.coords.latitude;
          const newLng = pos.coords.longitude;
          const moved =
            latRef.current == null ||
            Math.abs(newLat - latRef.current) > 0.004 ||
            Math.abs(newLng - lngRef.current) > 0.004;
          if (moved) {
            latRef.current = newLat;
            lngRef.current = newLng;
            const rev = await syncGpsToProfile(newLat, newLng);
            if (rev?.locality) setSessionAreaLabel(rev.locality);
            mlog('location', 'heartbeat_gps_refresh', {});
          }
        } catch {
          /* keep last known coords */
        }
      }
      if (latRef.current == null) return;
      api.put('/availability/location', {
        latitude:  latRef.current,
        longitude: lngRef.current,
        city_id:   cityRef.current,
      }).catch(() => {});
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

    if (wantAvailable) {
      const serverPin = String(user?.service_pincode || '').replace(/\D/g, '');
      if (serverPin.length !== 6) {
        setError('Set your service area to receive nearby shifts. Save your pincode in Profile first.');
        setToggling(false);
        return;
      }
    }

    let lat = null;
    let lng = null;

    let locSource = 'none';
    if (wantAvailable) {
      // Step 1: Try GPS (preferred — most accurate)
      try {
        const pos = await getGeoPosition({ highAccuracy: true, maxAge: 0 });
        lat = pos.coords.latitude;
        lng = pos.coords.longitude;
        latRef.current = lat;
        lngRef.current = lng;
        locSource = 'gps';
        mlog('availability', 'location_gps', {});
        const rev = await syncGpsToProfile(lat, lng);
        if (rev?.locality) setSessionAreaLabel(rev.locality);
      } catch (gpsErr) {
        // Step 2: GPS denied / unavailable — try stored pincode as fallback.
        // Without coordinates the dispatch engine excludes this nurse entirely,
        // so geocoding the pincode is critical for dispatch visibility.
        const storedPincode = loadPincode();
        if (storedPincode) {
          try {
            const result = await geocodePincode(storedPincode);
            lat = result.lat;
            lng = result.lng;
            latRef.current = lat;
            lngRef.current = lng;
            locSource = 'pincode';
            if (result.displayName) setSessionAreaLabel(result.displayName);
            mlog('availability', 'location_pincode_fallback', {});
          } catch {
            // Geocode failed — proceed without coords (nurse still goes online
            // but won't receive geo-filtered dispatch offers until location is set)
            mlog('availability', 'location_none', { gps_err: gpsErr.message });
          }
        } else {
          mlog('availability', 'location_none_no_pincode', { gps_err: gpsErr.message });
        }
      }
    }
    setLocationSource(wantAvailable ? locSource : 'none');

    try {
      const res = await api.put('/availability/toggle', {
        is_available: wantAvailable,
        latitude:     lat,
        longitude:    lng,
        city_id:      cityRef.current,
      });
      setIsAvailable(res.data.is_available);
      setPresenceState(res.data.presence_state);
      mlog('availability', wantAvailable ? 'toggle_online_ok' : 'toggle_offline_ok', {
        city_id: cityRef.current,
        loc_source: wantAvailable ? locSource : 'none',
      });
    } catch (err) {
      mlogError('availability', 'toggle_failed', err);
      const detail = err.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : 'Could not update availability. Please try again.');
    } finally {
      setToggling(false);
    }
  }, [toggling, isEligible, user?.service_pincode]);

  const value = {
    isAvailable,
    presenceState,
    cityId,
    loading,
    toggling,
    error,
    toggle,
    isEligible,
    locationSource,
    sessionAreaLabel,
    areaDisplayLabel:
      sessionAreaLabel ||
      user?.service_locality ||
      humanizeCityId(cityId) ||
      '',
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
