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

/** Immediate label (no network). */
export function formatAreaDisplaySync({ locality, pincode, cityId } = {}) {
  const loc = (locality || '').trim();
  if (loc) return loc;
  const pc = normalizeIndianPincode(pincode);
  if (pc) {
    const cached = getPersistedLocality(pc);
    if (cached) return cached;
  }
  const city = humanizeCityId(cityId);
  if (city && !pc) return city;
  return '';
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
