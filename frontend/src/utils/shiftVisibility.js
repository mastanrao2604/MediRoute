import { isBeforeShiftStartUtc } from './shiftDateTime';

const ACTIVE_SHIFT_STATUS = new Set(['open', 'dispatching']);

/** Job seeker browse list — open/dispatching shifts that have not started yet. */
export function isJobSeekerBrowsableShift(shift) {
  if (!shift) return false;
  if (!ACTIVE_SHIFT_STATUS.has(shift.status)) return false;
  if (shift.shift_start && !isBeforeShiftStartUtc(shift.shift_start)) return false;
  if (shift.assignment?.status === 'confirmed' || shift.assignment?.status === 'checked_in') {
    return false;
  }
  if (shift.my_offer?.status === 'accepted') return false;
  return true;
}

export function shiftHasRespondableOffer(shift) {
  return Boolean(shift?.my_offer?.respondable && shift.my_offer?.offer_id);
}

/** Phase 1: may be within ~50 km (server sets accept_eligible). */
export function shiftCanAccept(shift) {
  if (!shiftHasRespondableOffer(shift)) return false;
  if (shift.my_offer?.accept_eligible === false) return false;
  if (shift.accept_eligible === false) return false;
  return true;
}

export const SHIFT_ACCEPT_NEARBY_ONLY_MSG =
  'This shift is currently prioritizing nearby staff.';

export function filterJobSeekerShifts(shifts) {
  if (!Array.isArray(shifts)) return [];
  return shifts.filter(isJobSeekerBrowsableShift);
}

export function filterJobSeekerOffers(offers) {
  if (!Array.isArray(offers)) return [];
  return offers.filter((o) => o?.shift_start && isBeforeShiftStartUtc(o.shift_start));
}
