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
  const displayName = parseLocalityFromAddress(a, place.display_name).label;

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
    mlog('location', 'reverse_parse', {
      source: 'backend',
      micro_field: data.micro_field || null,
      city_field: data.city_field || null,
      locality: locality ? locality.slice(0, 48) : null,
      pin: pincode ? `${pincode.slice(0, 3)}***` : null,
    });
  } else {
    const url =
      `https://nominatim.openstreetmap.org/reverse` +
      `?lat=${la}&lon=${ln}&format=json&addressdetails=1&zoom=18`;

    const place = await nominatimFetch(url);
    const a = place.address || {};
    const rawPostcode = String(a.postcode || '').replace(/\D/g, '');
    pincode = rawPostcode.length >= 6 ? rawPostcode.slice(0, 6) : null;
    if (pincode && pincode.length !== 6) pincode = null;
    const parsed = parseLocalityFromAddress(a, place.display_name);
    locality = parsed.label || undefined;
    displayName = place.display_name;
    mlog('location', 'reverse_parse', {
      source: 'nominatim',
      micro_field: parsed.microField || null,
      city_field: parsed.cityField || null,
      address_keys: Object.keys(a).slice(0, 14).join(','),
      locality: locality ? locality.slice(0, 48) : null,
      pin: pincode ? `${pincode.slice(0, 3)}***` : null,
    });
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

/** OSM micro-area keys (Hyderabad suburbs often appear as city_district). */
const MICRO_LOCALITY_KEYS = [
  'suburb',
  'neighbourhood',
  'quarter',
  'residential',
  'locality',
  'city_district',
  'borough',
  'hamlet',
  'village',
];

const CITY_LABEL_KEYS = ['town', 'city', 'municipality'];

const SKIP_LOCALITY_RE =
  /^(india|telangana|andhra pradesh|hyderabad|hyd|greater hyderabad|telangana zone|\d{6})$/i;

function cleanPart(raw) {
  const v = String(raw || '').trim();
  if (!v || SKIP_LOCALITY_RE.test(v) || /\bzone$/i.test(v)) return '';
  return v;
}

function firstAddressField(a, keys) {
  for (const key of keys) {
    const value = cleanPart(a[key]);
    if (value) return { field: key, value };
  }
  return null;
}

/** Parse Nominatim display_name when address components lack suburb. */
export function localityFromDisplayName(displayNameFallback = '') {
  const parts = String(displayNameFallback || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
    .filter((p) => !SKIP_LOCALITY_RE.test(p) && !/^\d{6}$/.test(p));

  const cityIdx = parts.findIndex((p) => /^hyderabad$/i.test(p));
  if (cityIdx > 0) {
    const candidate = parts[cityIdx - 1];
    if (candidate && !/\b(road|street|lane|marg|highway|flyover)\b/i.test(candidate)) {
      return { field: 'display_name', value: candidate, city: 'Hyderabad' };
    }
  }

  for (const p of parts) {
    if (/\b(road|street|lane|marg|highway)\b/i.test(p)) continue;
    if (/^sec(tor)?\s*\d/i.test(p)) return { field: 'display_name', value: p };
    if (p.length >= 3 && p.length <= 40) return { field: 'display_name', value: p };
  }
  return null;
}

/**
 * Prefer suburb/neighbourhood/city_district over city-only labels.
 * @returns {{ label: string, microField?: string, cityField?: string }}
 */
export function parseLocalityFromAddress(a = {}, displayNameFallback = '') {
  const micro = firstAddressField(a, MICRO_LOCALITY_KEYS);
  let cityPart = firstAddressField(a, CITY_LABEL_KEYS);

  if (cityPart && micro && cityPart.value.toLowerCase() === micro.value.toLowerCase()) {
    cityPart = null;
  }

  if (micro) {
    const cityName = cityPart?.value || (micro.field === 'display_name' ? '' : '');
    const label =
      cityName && !/^hyderabad$/i.test(micro.value)
        ? `${micro.value}, ${cityName}`
        : micro.value;
    return {
      label,
      microField: micro.field,
      cityField: cityPart?.field,
    };
  }

  const fromDisplay = localityFromDisplayName(displayNameFallback);
  if (fromDisplay) {
    const label = fromDisplay.city
      ? `${fromDisplay.value}, ${fromDisplay.city}`
      : fromDisplay.value;
    return { label, microField: fromDisplay.field, cityField: fromDisplay.city ? 'city' : undefined };
  }

  if (cityPart) {
    return { label: cityPart.value, microField: undefined, cityField: cityPart.field };
  }

  return { label: '', microField: undefined, cityField: undefined };
}

/** @deprecated Use parseLocalityFromAddress — kept for callers expecting a string. */
export function formatLocalityFromAddress(a = {}, displayNameFallback = '') {
  return parseLocalityFromAddress(a, displayNameFallback).label;
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
