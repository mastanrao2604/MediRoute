/**
 * Unified GPS + permission flow (Web + Capacitor Android).
 * Used by job seeker availability/profile and recruiter Post Shift.
 */
import { Capacitor } from '@capacitor/core';
import { mlog, mlogError } from './mobileLogger';
import {
  reverseGeocodeCoords,
  normalizeIndianPincode,
  savePincode,
  saveLastKnownArea,
} from './geocodePincode';

export const LOCATION_AUDIENCE = {
  job_seeker: {
    permissionTitle: 'Find shifts near you',
    permissionBody:
      'Allow location access to receive nearby shift opportunities in your area (e.g. Madhapur, Kukatpally).',
    denied: 'Location is needed for nearby shift matching. You can retry or enter pincode in Profile.',
    permanent:
      'Location is turned off for MediRoute. Open Settings → Permissions → Location → Allow, then try again.',
  },
  recruiter: {
    permissionTitle: 'Hospital location',
    permissionBody:
      'Allow location access to auto-fill your hospital area and find nearby staff faster.',
    denied: 'Location helps post shifts in the right area. Retry or enter pincode manually.',
    permanent:
      'Location is turned off for MediRoute. Open Settings → Permissions → Location → Allow, then try again.',
  },
};

const DEFAULT_TIMEOUT_MS = 18_000;

async function getGeolocationPlugin() {
  if (!Capacitor.isNativePlatform()) return null;
  try {
    const { Geolocation } = await import('@capacitor/geolocation');
    return Geolocation;
  } catch (e) {
    mlogError('location', 'geolocation_plugin_missing', e);
    return null;
  }
}

/** @returns {'granted'|'denied'|'prompt'|'permanent'} */
export async function checkLocationPermission() {
  const Geo = await getGeolocationPlugin();
  if (Geo) {
    try {
      const st = await Geo.checkPermissions();
      const loc = st?.location || st?.coarseLocation || 'prompt';
      if (loc === 'granted') return 'granted';
      if (loc === 'denied') return 'permanent';
      return 'prompt';
    } catch {
      return 'prompt';
    }
  }
  if (!navigator.permissions?.query) return 'prompt';
  try {
    const r = await navigator.permissions.query({ name: 'geolocation' });
    if (r.state === 'granted') return 'granted';
    if (r.state === 'denied') return 'permanent';
    return 'prompt';
  } catch {
    return 'prompt';
  }
}

/** @returns {'granted'|'denied'|'permanent'} */
export async function requestLocationPermission() {
  const Geo = await getGeolocationPlugin();
  if (Geo) {
    try {
      const st = await Geo.requestPermissions();
      const loc = st?.location || st?.coarseLocation;
      mlog('location', 'native_permission', { loc });
      if (loc === 'granted') return 'granted';
      if (loc === 'denied') return 'permanent';
      return 'denied';
    } catch (e) {
      mlogError('location', 'native_permission_fail', e);
      return 'denied';
    }
  }
  return 'prompt';
}

function mapBrowserGeoError(err) {
  const code = err?.code;
  if (code === 1) return 'permanent';
  if (code === 2) return 'denied';
  if (code === 3) return 'timeout';
  return 'denied';
}

/**
 * Fetch coordinates. Throws { code, message } on failure.
 */
export async function getDevicePosition({
  highAccuracy = true,
  timeoutMs = DEFAULT_TIMEOUT_MS,
  maxAgeMs = 0,
  requestPermissionFirst = true,
} = {}) {
  if (!Capacitor.isNativePlatform() && !navigator.geolocation) {
    throw { code: 'unsupported', message: 'Location is not supported on this device.' };
  }

  if (requestPermissionFirst) {
    const perm = await checkLocationPermission();
    if (perm === 'prompt' || perm === 'denied') {
      const req = await requestLocationPermission();
      if (req !== 'granted' && perm !== 'granted') {
        throw {
          code: req === 'permanent' ? 'permanent' : 'denied',
          message: 'Location permission not granted.',
        };
      }
    }
    if (perm === 'permanent') {
      const req = await requestLocationPermission();
      if (req !== 'granted') {
        throw { code: 'permanent', message: 'Location permission blocked.' };
      }
    }
  }

  const Geo = await getGeolocationPlugin();
  if (Geo) {
    try {
      const pos = await Geo.getCurrentPosition({
        enableHighAccuracy: highAccuracy,
        timeout: timeoutMs,
        maximumAge: maxAgeMs,
      });
      mlog('location', 'native_gps_ok', {
        acc: pos.coords.accuracy,
      });
      return {
        lat: pos.coords.latitude,
        lng: pos.coords.longitude,
        accuracy: pos.coords.accuracy,
      };
    } catch (e) {
      mlogError('location', 'native_gps_fail', e);
      const msg = String(e?.message || e || '');
      if (/denied|permission/i.test(msg)) {
        throw { code: 'permanent', message: msg };
      }
      if (/timeout/i.test(msg)) {
        throw { code: 'timeout', message: 'Location timed out. Try again in an open area.' };
      }
      throw { code: 'denied', message: msg || 'Could not read GPS.' };
    }
  }

  return new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        mlog('location', 'web_gps_ok', {});
        resolve({
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
          accuracy: pos.coords.accuracy,
        });
      },
      (err) => {
        const code = mapBrowserGeoError(err);
        mlog('location', 'web_gps_fail', { code, msg: err?.message });
        reject({
          code,
          message:
            code === 'timeout'
              ? 'Location timed out. Try again.'
              : err?.message || 'Location unavailable.',
        });
      },
      {
        enableHighAccuracy: highAccuracy,
        timeout: timeoutMs,
        maximumAge: maxAgeMs,
      },
    );
  });
}

export function locationErrorMessage(err, audience = 'job_seeker') {
  const copy = LOCATION_AUDIENCE[audience] || LOCATION_AUDIENCE.job_seeker;
  const code = err?.code;
  if (code === 'permanent') return copy.permanent;
  if (code === 'timeout') return 'Location took too long. Check GPS is on and try again.';
  if (code === 'unsupported') return 'Location is not supported. Enter pincode manually.';
  if (code === 'reverse_failed') return 'Could not resolve your area name. Coordinates saved — enter pincode if needed.';
  if (code === 'no_pincode') return 'GPS worked but pincode was not found. Enter your 6-digit pincode manually.';
  if (code === 'denied') return copy.denied;
  return err?.message || copy.denied;
}

export async function openAppSettings() {
  const platform = Capacitor.getPlatform();
  mlog('location', 'open_settings', { platform });
  if (platform === 'android') {
    try {
      window.location.href =
        'intent:#Intent;action=android.settings.APPLICATION_DETAILS_SETTINGS;data=package:com.mediroute.app;end';
      return true;
    } catch {
      return false;
    }
  }
  if (platform === 'ios') {
    try {
      window.location.href = 'app-settings:';
      return true;
    } catch {
      return false;
    }
  }
  return false;
}

/**
 * Full capture: permission → GPS → reverse geocode.
 * @returns {{ ok: boolean, lat?, lng?, pincode?, locality?, permissionState?, error?, userMessage? }}
 */
export async function captureCurrentArea({
  audience = 'job_seeker',
  highAccuracy = true,
  syncProfile = false,
} = {}) {
  const copy = LOCATION_AUDIENCE[audience] || LOCATION_AUDIENCE.job_seeker;
  try {
    const { lat, lng } = await getDevicePosition({
      highAccuracy,
      requestPermissionFirst: true,
    });

    let rev;
    try {
      rev = await reverseGeocodeCoords(lat, lng);
    } catch (e) {
      mlogError('location', 'reverse_fail', e);
      return {
        ok: false,
        lat,
        lng,
        permissionState: 'granted',
        error: { code: 'reverse_failed' },
        userMessage: locationErrorMessage({ code: 'reverse_failed' }, audience),
      };
    }

    const pc = normalizeIndianPincode(rev.pincode);
    const locality = (rev.locality || '').trim();

    if (pc) savePincode(pc);
    saveLastKnownArea({ locality, pincode: pc, lat, lng, at: Date.now() });

    if (syncProfile && (pc || locality)) {
      try {
        const api = (await import('../api/axios')).default;
        await api.put('/profile/me', {
          ...(pc ? { service_pincode: pc } : {}),
          ...(locality ? { service_locality: locality } : {}),
          location_source: 'gps',
        });
        mlog('location', 'profile_sync_ok', { has_pin: Boolean(pc) });
      } catch (e) {
        mlogError('location', 'profile_sync_fail', e);
      }
    }

    if (!pc && !locality) {
      return {
        ok: false,
        lat,
        lng,
        permissionState: 'granted',
        error: { code: 'no_pincode' },
        userMessage: locationErrorMessage({ code: 'no_pincode' }, audience),
      };
    }

    return {
      ok: true,
      lat,
      lng,
      pincode: pc,
      locality,
      displayLabel: locality || (pc ? `Pin ${pc}` : ''),
      permissionState: 'granted',
      permissionTitle: copy.permissionTitle,
      permissionBody: copy.permissionBody,
    };
  } catch (e) {
    const userMessage = locationErrorMessage(e, audience);
    return {
      ok: false,
      permissionState: e?.code === 'permanent' ? 'permanent' : 'denied',
      error: e,
      userMessage,
      permissionTitle: copy.permissionTitle,
      permissionBody: copy.permissionBody,
    };
  }
}
