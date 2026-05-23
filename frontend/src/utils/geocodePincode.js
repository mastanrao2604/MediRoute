/**
 * Pincode / reverse geocoding — shared by AvailabilityContext, Profile, PostShift.
 * On native Android/iOS uses backend /geo/* proxy (CapacitorHttp breaks direct Nominatim).
 * Web uses Nominatim directly.
 */
import { Capacitor } from '@capacitor/core';
import { mlog, mlogError } from './mobileLogger';

const _cache = {};
const LOCALITY_MAP_KEY = 'mr_pin_locality_map';
const LAST_AREA_KEY = 'mr_last_known_area';

function readLocalityMap() {
  try {
    return JSON.parse(localStorage.getItem(LOCALITY_MAP_KEY) || '{}');
  } catch {
    return {};
  }
}

export function getPersistedLocality(pincode) {
  const pc = String(pincode || '').replace(/\D/g, '');
  if (pc.length !== 6) return '';
  const map = readLocalityMap();
  return map[pc] || '';
}

export function persistPincodeLocality(pincode, locality) {
  const pc = String(pincode || '').replace(/\D/g, '');
  const label = (locality || '').trim();
  if (pc.length !== 6 || !label) return;
  const map = readLocalityMap();
  map[pc] = label;
  try {
    localStorage.setItem(LOCALITY_MAP_KEY, JSON.stringify(map));
  } catch { /* quota */ }
}

export function saveLastKnownArea({ locality, pincode, lat, lng, at }) {
  try {
    localStorage.setItem(
      LAST_AREA_KEY,
      JSON.stringify({
        locality: (locality || '').trim(),
        pincode: normalizeIndianPincode(pincode),
        lat,
        lng,
        at: at || Date.now(),
      }),
    );
  } catch { /* ignore */ }
}

export function loadLastKnownArea() {
  try {
    const raw = localStorage.getItem(LAST_AREA_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

const NOMINATIM_HEADERS = {
  'Accept-Language': 'en',
  'User-Agent': 'MediRoute/1.0 (healthcare staffing; support@mediroute.in)',
};

async function nominatimFetch(url) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 14_000);
  try {
    const res = await fetch(url, { headers: NOMINATIM_HEADERS, signal: ctrl.signal });
    if (!res.ok) throw new Error('Geocode network error');
    return res.json();
  } catch (e) {
    if (e?.name === 'AbortError') throw new Error('Geocode timed out — check network and retry.');
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

async function backendGeoGet(path, params = {}) {
  const api = (await import('../api/axios')).default;
  try {
    const res = await api.get(path, { params, timeout: 16_000 });
    mlog('location', 'backend_geo_ok', { path });
    return res.data;
  } catch (e) {
    mlogError('location', 'backend_geo_fail', e, { path });
    const detail = e?.response?.data?.detail;
    if (typeof detail === 'string') throw new Error(detail);
    if (e?.code === 'ECONNABORTED') throw new Error('Geocode timed out — check network and retry.');
    throw new Error('Could not resolve location. Try again or enter pincode manually.');
  }
}

const useBackendGeo = () => Capacitor.isNativePlatform();

export async function geocodePincode(pincode) {
  const clean = String(pincode).replace(/\D/g, '');
  if (clean.length !== 6) throw new Error('Pincode must be 6 digits');
  if (_cache[clean]) return _cache[clean];

  if (useBackendGeo()) {
    const data = await backendGeoGet(`/geo/pincode/${clean}`);
    const result = {
      lat: parseFloat(data.lat),
      lng: parseFloat(data.lng),
      displayName: data.locality || data.display_name || '',
    };
    _cache[clean] = result;
    if (result.displayName) persistPincodeLocality(clean, result.displayName);
    return result;
  }

  const url =
    `https://nominatim.openstreetmap.org/search` +
    `?postalcode=${clean}&country=IN&format=json&limit=1&addressdetails=1`;

  const data = await nominatimFetch(url);
  if (!data.length) throw new Error('Pincode not found');

  const place = data[0];
  const a = place.address || {};
  const displayName = formatLocalityFromAddress(a, place.display_name);

  const result = {
    lat: parseFloat(place.lat),
    lng: parseFloat(place.lon),
    displayName,
  };
  _cache[clean] = result;
  persistPincodeLocality(clean, result.displayName);
  return result;
}

const _revCache = {};

/**
 * Reverse geocode lat/lng (one-shot). Returns { pincode (6-digit or null), locality }.
 */
export async function reverseGeocodeCoords(lat, lng) {
  const la = Number(lat);
  const ln = Number(lng);
  if (!Number.isFinite(la) || !Number.isFinite(ln)) {
    throw new Error('Invalid coordinates');
  }
  const key = `${la.toFixed(4)},${ln.toFixed(4)}`;
  if (_revCache[key]) return _revCache[key];

  let pincode = null;
  let locality;
  let displayName;

  if (useBackendGeo()) {
    const data = await backendGeoGet('/geo/reverse', { lat: la, lng: ln });
    pincode = normalizeIndianPincode(data.pincode);
    locality = (data.locality || '').trim() || undefined;
    displayName = data.display_name;
  } else {
    const url =
      `https://nominatim.openstreetmap.org/reverse` +
      `?lat=${la}&lon=${ln}&format=json&addressdetails=1`;

    const place = await nominatimFetch(url);
    const a = place.address || {};
    const rawPostcode = String(a.postcode || '').replace(/\D/g, '');
    pincode = rawPostcode.length >= 6 ? rawPostcode.slice(0, 6) : null;
    if (pincode && pincode.length !== 6) pincode = null;
    locality = formatLocalityFromAddress(a, place.display_name) || undefined;
    displayName = place.display_name;
  }

  const out = {
    pincode,
    locality,
    displayName,
  };
  _revCache[key] = out;
  if (out.locality || out.pincode) {
    saveLastKnownArea({
      locality: out.locality,
      pincode: out.pincode,
      lat: la,
      lng: ln,
      at: Date.now(),
    });
    if (out.pincode && out.locality) persistPincodeLocality(out.pincode, out.locality);
  }
  return out;
}

/** Prefer suburb/neighbourhood over city codes for display. */
export function formatLocalityFromAddress(a = {}, displayNameFallback = '') {
  const suburb = a.suburb || a.neighbourhood || a.quarter || a.residential;
  const town = a.city || a.town || a.village || a.municipality;
  if (suburb && town && suburb !== town) {
    return `${suburb}, ${town}`;
  }
  if (suburb) return suburb;
  if (town) return town;
  const parts = (displayNameFallback || '').split(',').map((s) => s.trim()).filter(Boolean);
  if (parts.length >= 2) return parts.slice(0, 2).join(', ');
  return parts[0] || '';
}

/** Canonical 6-digit Indian pincode or null */
export function normalizeIndianPincode(raw) {
  const c = String(raw || '').replace(/\D/g, '');
  return c.length === 6 ? c : null;
}

// Pincode stored per-user in localStorage
const STORAGE_KEY = 'mr_nurse_pincode';

export function savePincode(pincode) {
  if (pincode) localStorage.setItem(STORAGE_KEY, String(pincode).replace(/\D/g, ''));
}

export function loadPincode() {
  return localStorage.getItem(STORAGE_KEY) || '';
}
