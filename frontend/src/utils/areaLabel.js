/**
 * User-facing area labels — locality-first (never raw city_id like "HYD").
 */
import {
  geocodePincode,
  normalizeIndianPincode,
  reverseGeocodeCoords,
  getPersistedLocality,
  persistPincodeLocality,
  saveLastKnownArea,
  loadLastKnownArea,
} from './geocodePincode';
import { mlog } from './mobileLogger';

const CITY_ID_LABELS = {
  HYD: 'Hyderabad',
};

/** Hide internal shard codes from UI. */
export function humanizeCityId(cityId) {
  if (!cityId) return '';
  const key = String(cityId).trim().toUpperCase();
  return CITY_ID_LABELS[key] || '';
}

/**
 * User-facing area label — locality first, never raw pincode as primary.
 * Pincode stays internal unless includePincode is true (compact secondary).
 */
export function formatAreaDisplaySync({
  locality,
  pincode,
  cityId,
  includePincode = false,
} = {}) {
  const pc = normalizeIndianPincode(pincode);
  const stored = (locality || '').trim();
  const cached = pc ? getPersistedLocality(pc) : '';
  const last = loadLastKnownArea();
  const lastMatch = pc && last?.pincode === pc ? (last.locality || '').trim() : '';

  let primary = stored || cached || lastMatch || '';
  if (!primary) {
    primary = humanizeCityId(cityId) || (pc ? 'Hyderabad' : '');
  }
  if (!primary) return '';

  if (includePincode && pc) {
    return `${primary} • ${pc}`;
  }
  return primary;
}

/** Resolve best area label (GPS → stored locality → pincode geocode → last known). */
export async function resolveAreaLabel({ locality, pincode, lat, lng, cityId } = {}) {
  const sync = formatAreaDisplaySync({ locality, pincode, cityId });
  if (sync) return sync;

  const pc = normalizeIndianPincode(pincode);
  if (pc) {
    try {
      const g = await geocodePincode(pc);
      persistPincodeLocality(pc, g.displayName);
      mlog('location', 'pincode_locality_ok', { pin: pc.slice(0, 3) + '***' });
      return g.displayName;
    } catch (e) {
      mlog('location', 'pincode_locality_fail', { err: e?.message });
    }
  }

  if (lat != null && lng != null) {
    try {
      const rev = await reverseGeocodeCoords(lat, lng);
      const label = (rev.locality || '').trim();
      if (label) {
        saveLastKnownArea({
          locality: label,
          pincode: rev.pincode || pc,
          lat,
          lng,
          at: Date.now(),
        });
        if (rev.pincode) persistPincodeLocality(rev.pincode, label);
        mlog('location', 'reverse_locality_ok', {});
        return label;
      }
    } catch (e) {
      mlog('location', 'reverse_locality_fail', { err: e?.message });
    }
  }

  const last = loadLastKnownArea();
  if (last?.locality) return last.locality;

  if (pc) {
    const cached = getPersistedLocality(pc);
    if (cached) return cached;
    return humanizeCityId(cityId) || 'Hyderabad';
  }

  return humanizeCityId(cityId) || '';
}

export function shiftAreaSource(shift) {
  if (!shift) return {};
  return {
    locality: shift.hospital_locality,
    pincode: shift.hospital_pincode,
    lat: shift.hospital_latitude,
    lng: shift.hospital_longitude,
    cityId: shift.city_id,
  };
}

export function profileAreaSource(user) {
  if (!user) return {};
  return {
    locality: user.service_locality,
    pincode: user.service_pincode,
    cityId: 'HYD',
  };
}
