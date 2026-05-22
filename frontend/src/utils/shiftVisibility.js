import { isBeforeShiftStartUtc } from './shiftDateTime';

const ACTIVE_SHIFT_STATUS = new Set(['open', 'dispatching']);

/** Job seeker browse list — open/dispatching shifts that have not started yet. */
export function isJobSeekerBrowsableShift(shift) {
  if (!shift) return false;
  if (!ACTIVE_SHIFT_STATUS.has(shift.status)) return false;
  if (shift.shift_start && !isBeforeShiftStartUtc(shift.shift_start)) return false;
  return true;
}

export function filterJobSeekerShifts(shifts) {
  if (!Array.isArray(shifts)) return [];
  return shifts.filter(isJobSeekerBrowsableShift);
}

export function filterJobSeekerOffers(offers) {
  if (!Array.isArray(offers)) return [];
  return offers.filter((o) => o?.shift_start && isBeforeShiftStartUtc(o.shift_start));
}
