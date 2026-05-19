/** User-facing copy for realtime staffing (no backend/engineering terms). */

export const SEARCH_PHASE_LABEL = {
  dispatching: 'Contacting available nurses…',
  no_candidates: 'Expanding search to nearby areas…',
  waiting: 'Waiting for nurse responses…',
  timed_out: 'Still searching — reaching more nurses…',
  watching: 'Still looking for available nurses…',
  watching_online: 'Nurses came online — notifying them now…',
};

export const SHIFT_CARD_STATUS = {
  open: 'Getting ready',
  dispatching: 'Finding staff',
  filled: 'Staff confirmed',
  expired: 'Shift expired',
  cancelled: 'Cancelled',
};

export function formatNursesContacted(count) {
  if (count == null) return null;
  return `${count} nurse${count === 1 ? '' : 's'} contacted`;
}

export function formatSearchDistanceKm(km) {
  if (km == null) return null;
  return `Searching within ${Number(km).toFixed(0)} km`;
}

export function isPastShiftStart(iso) {
  if (!iso) return false;
  return new Date(iso).getTime() <= Date.now();
}
