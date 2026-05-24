/**
 * Unified GPS + permission flow (Web + Capacitor Android).
 * Used by job seeker availability/profile and recruiter Post Shift.
 *
 * Android note: separate checkPermissions/requestPermissions can hang on some
 * devices (Moto G84). We call getCurrentPosition directly and use WebView GPS
 * as fallback with hard timeouts.
 */
import { Capacitor } from '@capacitor/core';
import { Geolocation } from '@capacitor/geolocation';
import { mlog, mlogError } from './mobileLogger';
import {
  reverseGeocodeCoords,
  normalizeIndianPincode,
  savePincode,
  saveLastKnownArea,
  gracefulAreaFallback,
  loadLastKnownArea,
} from './geocodePincode';
import { formatAreaDisplaySync } from './areaLabel';

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
const PERM_PROBE_TIMEOUT_MS = 6_000;

function withTimeout(promise, ms, label) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(
      () => reject({ code: 'timeout', message: `${label} timed out after ${Math.round(ms / 1000)}s` }),
      ms,
    );
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

function nativeLocationGranted(st) {
  const fine = st?.location;
  const coarse = st?.coarseLocation;
  return fine === 'granted' || coarse === 'granted';
}

function mapBrowserGeoError(err) {
  const code = err?.code;
  if (code === 1) return 'permanent';
  if (code === 2) return 'denied';
  if (code === 3) return 'timeout';
  return 'denied';
}

function getBrowserPosition({ highAccuracy, timeoutMs, maxAgeMs }) {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject({ code: 'unsupported', message: 'Location is not supported on this device.' });
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        mlog('location', 'webview_gps_ok', { acc: pos.coords.accuracy });
        resolve({
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
          accuracy: pos.coords.accuracy,
        });
      },
      (err) => {
        const code = mapBrowserGeoError(err);
        mlog('location', 'webview_gps_fail', { code, msg: err?.message });
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

async function getCapacitorPosition({ highAccuracy, timeoutMs, maxAgeMs }) {
  const tryGet = (accurate) =>
    Geolocation.getCurrentPosition({
      enableHighAccuracy: accurate,
      timeout: timeoutMs,
      maximumAge: maxAgeMs,
    });

  try {
    const pos = await tryGet(highAccuracy);
    mlog('location', 'native_gps_ok', { acc: pos.coords.accuracy });
    return {
      lat: pos.coords.latitude,
      lng: pos.coords.longitude,
      accuracy: pos.coords.accuracy,
    };
  } catch (firstErr) {
    if (!highAccuracy) throw firstErr;
    mlog('location', 'native_gps_retry_low_accuracy', {
      msg: String(firstErr?.message || firstErr || ''),
    });
    const pos = await tryGet(false);
    mlog('location', 'native_gps_ok', { acc: pos.coords.accuracy, low: true });
    return {
      lat: pos.coords.latitude,
      lng: pos.coords.longitude,
      accuracy: pos.coords.accuracy,
    };
  }
}

/** Warm up plugin; failures are ignored. */
export async function warmUpLocationPlugin() {
  if (!Capacitor.isNativePlatform()) return;
  try {
    await withTimeout(
      Geolocation.checkPermissions(),
      PERM_PROBE_TIMEOUT_MS,
      'perm_warmup',
    );
    mlog('location', 'plugin_warmup_ok', {});
  } catch (e) {
    mlog('location', 'plugin_warmup_skip', { msg: e?.message || String(e) });
  }
}

/** @returns {'granted'|'denied'|'prompt'|'permanent'} */
export async function checkLocationPermission() {
  if (Capacitor.isNativePlatform()) {
    try {
      const st = await withTimeout(
        Geolocation.checkPermissions(),
        PERM_PROBE_TIMEOUT_MS,
        'perm_check',
      );
      mlog('location', 'native_perm_check', {
        location: st?.location,
        coarse: st?.coarseLocation,
      });
      if (nativeLocationGranted(st)) return 'granted';
      if (st?.location === 'denied' && st?.coarseLocation === 'denied') return 'permanent';
      return 'prompt';
    } catch (e) {
      mlog('location', 'native_perm_check_timeout', { msg: e?.message });
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
  if (Capacitor.isNativePlatform()) {
    try {
      const st = await withTimeout(
        Geolocation.requestPermissions(),
        45_000,
        'perm_request',
      );
      mlog('location', 'native_permission', {
        location: st?.location,
        coarse: st?.coarseLocation,
      });
      if (nativeLocationGranted(st)) return 'granted';
      if (st?.location === 'denied' && st?.coarseLocation === 'denied') return 'permanent';
      return 'denied';
    } catch (e) {
      mlog('location', 'native_permission_timeout', { msg: e?.message });
      return 'denied';
    }
  }
  return 'prompt';
}

/**
 * Fetch coordinates. Throws { code, message } on failure.
 * On native: Capacitor GPS first, then WebView geolocation fallback.
 */
export async function getDevicePosition({
  highAccuracy = true,
  timeoutMs = DEFAULT_TIMEOUT_MS,
  maxAgeMs = 0,
  requestPermissionFirst = false,
} = {}) {
  if (!Capacitor.isNativePlatform() && !navigator.geolocation) {
    throw { code: 'unsupported', message: 'Location is not supported on this device.' };
  }

  mlog('location', 'gps_begin', { native: Capacitor.isNativePlatform() });

  if (requestPermissionFirst) {
    const perm = await checkLocationPermission();
    if (perm === 'granted') {
      /* continue */
    } else if (perm === 'permanent') {
      const req = await requestLocationPermission();
      if (req !== 'granted') {
        throw { code: 'permanent', message: 'Location permission blocked.' };
      }
    } else {
      const req = await requestLocationPermission();
      if (req !== 'granted') {
        throw {
          code: req === 'permanent' ? 'permanent' : 'denied',
          message: 'Location permission not granted.',
        };
      }
    }
  }

  const opts = { highAccuracy, timeoutMs, maxAgeMs };
  const capMs = timeoutMs + 4_000;
  const webMs = timeoutMs + 4_000;

  if (Capacitor.isNativePlatform()) {
    try {
      return await withTimeout(getCapacitorPosition(opts), capMs, 'capacitor_gps');
    } catch (e) {
      mlogError('location', 'capacitor_gps_fail', e);
      mlog('location', 'gps_fallback_webview', {});
      try {
        return await withTimeout(getBrowserPosition(opts), webMs, 'webview_gps');
      } catch (webErr) {
        mlogError('location', 'webview_gps_fail', webErr);
        throw webErr?.code ? webErr : e;
      }
    }
  }

  return withTimeout(getBrowserPosition(opts), webMs, 'web_gps');
}

export function locationErrorMessage(err, audience = 'job_seeker') {
  const copy = LOCATION_AUDIENCE[audience] || LOCATION_AUDIENCE.job_seeker;
  const code = err?.code;
  if (code === 'permanent') return copy.permanent;
  if (code === 'timeout') {
    return 'Location took too long. Allow location permission, turn on GPS, and try again.';
  }
  if (code === 'unsupported') return 'Location is not supported. Enter pincode manually.';
  if (code === 'reverse_failed') {
    return 'We saved your GPS location. Add your 6-digit pincode below if your area name did not appear.';
  }
  if (code === 'no_pincode') {
    return 'Area name updated. Add your 6-digit pincode for best nearby matching.';
  }
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

const CAPTURE_BUDGET_MS = 42_000;

/**
 * Full capture: permission → GPS → reverse geocode.
 */
export async function captureCurrentArea({
  audience = 'job_seeker',
  highAccuracy = true,
  syncProfile = false,
} = {}) {
  const copy = LOCATION_AUDIENCE[audience] || LOCATION_AUDIENCE.job_seeker;
  mlog('location', 'capture_start', { audience, syncProfile });

  const run = async () => {
    const { lat, lng } = await getDevicePosition({
      highAccuracy,
      requestPermissionFirst: false,
    });

    let rev;
    try {
      rev = await reverseGeocodeCoords(lat, lng);
    } catch (e) {
      mlogError('location', 'reverse_fail', e);
      rev = gracefulAreaFallback(lat, lng);
      mlog('location', 'reverse_graceful', { source: rev.source || 'unknown' });
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
      mlog('location', 'capture_no_area_label', {});
      return {
        ok: true,
        lat,
        lng,
        pincode: null,
        locality: '',
        displayLabel: '',
        permissionState: 'granted',
        needsPincode: true,
        userMessage: locationErrorMessage({ code: 'no_pincode' }, audience),
      };
    }

    if (!pc && locality) {
      mlog('location', 'capture_ok_locality_only', {});
    }
    mlog('location', 'capture_ok', { has_pin: Boolean(pc), has_locality: Boolean(locality) });
    return {
      ok: true,
      lat,
      lng,
      pincode: pc,
      locality,
      displayLabel: formatAreaDisplaySync({
        locality,
        pincode: pc,
        cityId: 'HYD',
      }),
      permissionState: 'granted',
      permissionTitle: copy.permissionTitle,
      permissionBody: copy.permissionBody,
    };
  };

  try {
    return await withTimeout(run(), CAPTURE_BUDGET_MS, 'capture');
  } catch (e) {
    mlog('location', 'capture_fail', { code: e?.code, msg: e?.message });
    const last = loadLastKnownArea();
    const lastFresh = last?.at && Date.now() - Number(last.at) < 24 * 60 * 60 * 1000;
    if (
      last?.lat != null
      && last?.lng != null
      && lastFresh
      && e?.code !== 'permanent'
    ) {
      mlog('location', 'capture_fallback_last_known', {});
      const locality = last.locality || '';
      const pc = normalizeIndianPincode(last.pincode);
      return {
        ok: true,
        lat: last.lat,
        lng: last.lng,
        pincode: pc,
        locality,
        displayLabel: formatAreaDisplaySync({ locality, pincode: pc, cityId: 'HYD' }),
        permissionState: 'granted',
        degraded: true,
        permissionTitle: copy.permissionTitle,
        permissionBody: copy.permissionBody,
      };
    }
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
