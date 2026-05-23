/** User-facing copy for realtime staffing (shared recruiter + employee UI). */

export const SEARCH_PHASE_LABEL = {
  dispatching: 'Contacting available nurses…',
  no_candidates: 'Expanding search to nearby areas…',
  waiting: 'Waiting for nurse responses…',
  timed_out: 'Still searching — reaching more nurses…',
  watching: 'Still looking for available nurses…',
  watching_online: 'Nurses came online — notifying them now…',
  receiving: 'Receiving applications — more nurses can still apply',
  active: 'Staff search active',
};

/** Recruiter shift card / list status */
export const SHIFT_CARD_STATUS = {
  open: 'Getting ready',
  dispatching: 'Finding staff',
  filled: 'Staff finalized',
  search_paused: 'Search paused',
  receiving: 'Receiving applications',
  expired: 'Shift expired',
  cancelled: 'Cancelled',
};

/** Recruiter card — search still running with at least one confirm. */
export function recruiterShiftCardStatus(shift, live) {
  const confirmed = shift?.confirmed_count ?? (shift?.applicants?.length || 0);
  const searchActive = shift?.search_active !== false && !shift?.search_closed;
  if (shift?.search_closed && confirmed > 0) return 'search_paused';
  if (searchActive && confirmed > 0) return 'receiving';
  if (shift?.status === 'filled' && !searchActive) return 'filled';
  if (shift?.status === 'dispatching' || shift?.status === 'open') return 'dispatching';
  if (live?.type === 'nurse_accepted' && searchActive) return 'receiving';
  return shift?.status || 'open';
}

/** Employee browse list — same labels as recruiter for consistency */
export const SHIFT_BROWSE_STATUS = SHIFT_CARD_STATUS;

export const URGENCY_LABEL = {
  emergency: { label: 'Right Now', color: 'bg-red-100 text-red-700' },
  urgent: { label: 'Within Few Hours', color: 'bg-orange-100 text-orange-700' },
  standard: { label: "Today's Shift", color: 'bg-blue-100 text-blue-700' },
  planned: { label: 'Plan Ahead', color: 'bg-gray-100 text-gray-700' },
};

export function urgencyLabel(urgency) {
  return URGENCY_LABEL[urgency]?.label || "Today's Shift";
}

export function shiftStatusLabel(status) {
  return SHIFT_CARD_STATUS[status] || (status ? String(status).replace(/_/g, ' ') : '—');
}

export function formatRoleLabel(role) {
  if (!role) return '';
  return String(role).replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Nurse dashboard — their confirmed shift progress */
export const NURSE_ASSIGNMENT_STATUS = {
  confirmed: 'Confirmed',
  checked_in: 'On shift',
  completed: 'Completed',
  no_show: 'Missed',
  cancelled: 'Cancelled',
};

export function nurseAssignmentStatusLabel(status) {
  return NURSE_ASSIGNMENT_STATUS[status] || 'Active';
}

export function formatNursesContacted(count) {
  if (count == null) return null;
  return `${count} nurse${count === 1 ? '' : 's'} contacted`;
}

export function formatSearchDistanceKm(km) {
  if (km == null) return null;
  return `Searching within ${Number(km).toFixed(0)} km`;
}

/** Map API / engine errors to plain language for nurses & recruiters. */
export function humanizeStaffingError(message) {
  if (!message || typeof message !== 'string') {
    return 'Something went wrong. Please try again.';
  }
  const m = message.trim();
  const rules = [
    [/this offer has expired/i, 'This invitation has expired.'],
    [/offer has expired/i, 'This invitation has expired.'],
    [/no longer accepting assignments/i, 'This shift is already filled or closed.'],
    [/already have an active assignment/i, 'Finish your current shift before accepting another.'],
    [/shift is no longer/i, 'This shift is no longer available.'],
    [/access denied/i, 'This shift is no longer available.'],
    [/no longer available/i, 'This shift is no longer available.'],
    [/prioritizing nearby staff/i, 'This shift is currently prioritizing nearby staff.'],
    [/available only for nearby staff/i, 'This shift is currently available only for nearby staff.'],
    [/not available on the server/i, 'This feature is not available yet. Please update the app or try again later.'],
    [/dispatch is already active/i, 'Staff search is already running for this shift.'],
    [/please choose a new future shift time/i, 'Please choose a new future shift time to repost this requirement.'],
    [/shift start time has passed/i, 'Please choose a new future shift time to repost this requirement.'],
    [/failed to load jobs/i, 'Could not load job listings'],
    [/network error/i, 'Could not reach the server. Check your connection.'],
  ];
  for (const [pattern, replacement] of rules) {
    if (pattern.test(m)) return replacement;
  }
  if (/dispatch/i.test(m)) {
    return m.replace(/dispatch/gi, 'staffing').replace(/Dispatch/g, 'Staffing');
  }
  return m;
}

export { isPastShiftStartUtc as isPastShiftStart } from './shiftDateTime';
