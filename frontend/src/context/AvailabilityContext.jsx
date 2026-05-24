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
  resolveAreaLabel,
  getPersistedLocality,
} from '../utils/geocodePincode';
import { getDevicePosition, captureCurrentArea } from '../utils/deviceLocation';
import { formatAreaDisplaySync, humanizeCityId } from '../utils/areaLabel';
import { mlog, mlogError } from '../utils/mobileLogger';

const HEARTBEAT_INTERVAL_MS = 90_000;

// Must mirror DISPATCH_ELIGIBLE_ROLES in backend availability.py
export const DISPATCH_ELIGIBLE_ROLES = new Set([
  'nurse', 'staff_nurse', 'icu_nurse', 'ot_nurse', 'emergency_nurse',
  'home_care_nurse', 'doctor', 'lab_tech', 'pharmacist', 'driver', 'front_office',
]);

const AvailabilityContext = createContext(null);

function getGeoPosition(opts) {
  return getDevicePosition({
    highAccuracy: opts?.highAccuracy ?? true,
    maxAgeMs: opts?.maxAge ?? 45_000,
    requestPermissionFirst: true,
  }).then(({ lat, lng }) => ({
    coords: { latitude: lat, longitude: lng },
  }));
}

async function syncGpsToProfile(lat, lng) {
  try {
    const rev = await reverseGeocodeCoords(lat, lng);
    const pc = normalizeIndianPincode(rev.pincode);
    const label = resolveAreaLabel({
      locality: rev.locality,
      pincode: pc,
      displayName: rev.displayName,
      lat,
      lng,
    });
    if (label || pc) {
      saveLastKnownArea({ locality: label || rev.locality, pincode: pc, lat, lng });
    }
    if (pc) savePincode(pc);
    if (pc || label) {
      await api.put('/profile/me', {
        ...(pc ? { service_pincode: pc } : {}),
        ...(label ? { service_locality: label } : {}),
        location_source: 'gps',
      });
      mlog('location', 'profile_gps_sync_ok', { has_pin: Boolean(pc), has_locality: Boolean(label) });
    }
    return { ...rev, locality: label || rev.locality };
  } catch (e) {
    mlog('location', 'profile_gps_sync_fail', { err: e?.message });
    return null;
  }
}

function labelFromCapture(cap, lat, lng) {
  return resolveAreaLabel({
    locality: cap.locality,
    pincode: cap.pincode,
    displayName: cap.displayName || cap.displayLabel,
    lat,
    lng,
  }) || loadLastKnownArea()?.locality || '';
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
  const [locRefreshing, setLocRefreshing] = useState(false);

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
        if (res.data.is_available) {
          if (res.data.latitude != null && res.data.longitude != null) {
            setLocationSource('gps');
          } else {
            const pin = normalizeIndianPincode(user?.service_pincode);
            if (pin) {
              setLocationSource('pincode');
            } else if (user?.location_source === 'gps' || user?.location_source === 'pincode') {
              setLocationSource(user.location_source);
            } else {
              const last = loadLastKnownArea();
              if (last?.lat != null && last?.lng != null) {
                setLocationSource('gps');
              }
            }
          }
        }
      })
      .catch(() => {}) // non-critical — default to offline state
      .finally(() => setLoading(false));

    const last = loadLastKnownArea();
    if (last?.locality) setSessionAreaLabel(last.locality);
    else if (user?.service_locality) setSessionAreaLabel(user.service_locality);
  }, [user?.id, isEligible, user?.service_locality, user?.service_pincode, user?.location_source]);

  // Restore area label when app returns to foreground (reconnect / reopen).
  useEffect(() => {
    if (!isEligible) return undefined;
    const onVisible = () => {
      if (document.visibilityState !== 'visible') return;
      const last = loadLastKnownArea();
      if (last?.locality) {
        setSessionAreaLabel((prev) => prev || last.locality);
        mlog('location', 'visibility_area_restore', { has_locality: true });
      }
      if (isAvailable && last?.lat != null && last?.lng != null) {
        latRef.current = last.lat;
        lngRef.current = last.lng;
        setLocationSource((prev) => (prev === 'none' ? 'gps' : prev));
      }
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [isEligible, isAvailable]);

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
            setLocationSource('gps');
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

  const refreshLocation = useCallback(async () => {
    if (!isEligible || locRefreshing) return;
    setLocRefreshing(true);
    setError('');
    try {
      const cap = await captureCurrentArea({
        audience: 'job_seeker',
        highAccuracy: true,
        syncProfile: true,
      });
      if (cap.ok) {
        latRef.current = cap.lat;
        lngRef.current = cap.lng;
        const label = labelFromCapture(cap, cap.lat, cap.lng);
        if (label) setSessionAreaLabel(label);
        setLocationSource('gps');
        if (isAvailable && cap.lat != null) {
          await api.put('/availability/location', {
            latitude: cap.lat,
            longitude: cap.lng,
            city_id: cityRef.current,
          });
        }
        window.dispatchEvent(new CustomEvent('mr-jobs-shifts-refresh'));
        mlog('location', 'manual_refresh_ok', {
          has_pin: Boolean(cap.pincode),
          has_locality: Boolean(label),
        });
      } else {
        setError(cap.userMessage || 'Could not update your location.');
      }
    } catch (e) {
      mlogError('location', 'manual_refresh_fail', e);
      setError('Could not update your location. Try again.');
    } finally {
      setLocRefreshing(false);
    }
  }, [isEligible, locRefreshing, isAvailable]);

  // ── Toggle availability ───────────────────────────────────────────────────
  const toggle = useCallback(async (wantAvailable) => {
    if (toggling || !isEligible) return;
    setToggling(true);
    setError('');

    let lat = null;
    let lng = null;
    let locSource = 'none';
    if (wantAvailable) {
      const cap = await captureCurrentArea({
        audience: 'job_seeker',
        highAccuracy: true,
        syncProfile: true,
      });
      if (cap.ok) {
        lat = cap.lat;
        lng = cap.lng;
        latRef.current = lat;
        lngRef.current = lng;
        locSource = 'gps';
        const label = labelFromCapture(cap, lat, lng);
        if (label) setSessionAreaLabel(label);
        mlog('availability', 'location_gps', { has_locality: Boolean(label), has_pin: Boolean(cap.pincode) });
      } else if (cap.lat != null && cap.lng != null) {
        lat = cap.lat;
        lng = cap.lng;
        latRef.current = lat;
        lngRef.current = lng;
        locSource = 'gps';
        const label = labelFromCapture(cap, lat, lng);
        if (label) setSessionAreaLabel(label);
        mlog('availability', 'location_gps_partial', { has_locality: Boolean(label), has_pin: Boolean(cap.pincode) });
      } else {
        const serverPin = String(user?.service_pincode || '').replace(/\D/g, '');
        const storedPincode = loadPincode() || serverPin;
        if (storedPincode.length === 6) {
          try {
            const result = await geocodePincode(storedPincode);
            lat = result.lat;
            lng = result.lng;
            latRef.current = lat;
            lngRef.current = lng;
            locSource = 'pincode';
            const pinLabel = result.displayName || getPersistedLocality(storedPincode);
            if (pinLabel) setSessionAreaLabel(pinLabel);
            mlog('availability', 'location_pincode_fallback', { has_locality: Boolean(pinLabel) });
          } catch {
            mlog('availability', 'location_none', {});
          }
        }
        if (!lat && cap.userMessage) {
          setError(cap.userMessage);
          setToggling(false);
          return;
        }
        if (!lat && storedPincode.length !== 6) {
          setError('Set your service area to receive nearby shifts. Use GPS or save pincode in Profile.');
          setToggling(false);
          return;
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
    areaDisplayLabel: formatAreaDisplaySync({
      locality: sessionAreaLabel || user?.service_locality,
      pincode: user?.service_pincode,
      cityId: cityId,
    }),
    refreshLocation,
    locRefreshing,
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
