/**
 * Nominatim pincode geocoding — shared by AvailabilityContext and PostShift.
 *
 * Returns { lat, lng, displayName } or throws on failure.
 * Cached in memory for the session (same pincode → no repeat network call).
 */

const _cache = {};

export async function geocodePincode(pincode) {
  const clean = String(pincode).replace(/\D/g, '');
  if (clean.length !== 6) throw new Error('Pincode must be 6 digits');
  if (_cache[clean]) return _cache[clean];

  const url =
    `https://nominatim.openstreetmap.org/search` +
    `?postalcode=${clean}&country=IN&format=json&limit=1&addressdetails=1`;

  const res = await fetch(url, { headers: { 'Accept-Language': 'en' } });
  if (!res.ok) throw new Error('Geocode network error');
  const data = await res.json();
  if (!data.length) throw new Error('Pincode not found');

  const place = data[0];
  const a = place.address || {};
  const parts = [
    a.suburb || a.neighbourhood || a.village || a.town,
    a.city || a.county || a.state_district,
  ].filter(Boolean);
  const displayName =
    parts.length ? parts.join(', ') : (place.display_name?.split(',')[0] || 'Area found');

  const result = {
    lat: parseFloat(place.lat),
    lng: parseFloat(place.lon),
    displayName,
  };
  _cache[clean] = result;
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

  const url =
    `https://nominatim.openstreetmap.org/reverse` +
    `?lat=${la}&lon=${ln}&format=json&addressdetails=1`;

  const res = await fetch(url, { headers: { 'Accept-Language': 'en' } });
  if (!res.ok) throw new Error('Reverse geocode network error');
  const place = await res.json();
  const a = place.address || {};
  const rawPostcode = String(a.postcode || '').replace(/\D/g, '');
  let pincode = rawPostcode.length >= 6 ? rawPostcode.slice(0, 6) : null;
  if (pincode && pincode.length !== 6) pincode = null;

  const localityParts = [
    a.suburb || a.neighbourhood || a.village,
    a.city || a.town || a.state_district,
  ].filter(Boolean);
  const locality = localityParts.length
    ? localityParts.join(', ')
    : (place.display_name || '').split(',').slice(0, 2).join(',').trim() || '';

  const out = {
    pincode,
    locality: locality || undefined,
    displayName: place.display_name,
  };
  _revCache[key] = out;
  return out;
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
