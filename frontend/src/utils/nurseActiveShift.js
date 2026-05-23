import { formatShiftDateTime } from './shiftDateTime';
import {
  formatRoleLabel,
  nurseAssignmentStatusLabel,
  urgencyLabel,
  isApplicationFinalized,
  isApplicationPending,
  APPLICATION_STATUS_LABEL,
} from './staffingStatusCopy';
import { formatAreaDisplaySync, shiftAreaSource } from './areaLabel';

const ACTIVE_ASSIGNMENT = new Set(['confirmed', 'checked_in']);
const TERMINAL_ASSIGNMENT = new Set(['cancelled', 'completed', 'no_show']);
const TERMINAL_SHIFT = new Set(['cancelled', 'expired']);

/** Pick the nurse's current operational shift (most recent active assignment). */
export function pickActiveNurseShift(shifts) {
  if (!Array.isArray(shifts)) return null;
  for (const shift of shifts) {
    const a = shift?.assignment;
    if (!a || TERMINAL_ASSIGNMENT.has(a.status)) continue;
    if (TERMINAL_SHIFT.has(shift.status)) continue;
    if (isApplicationPending(shift) || isApplicationFinalized(shift)) {
      return shift;
    }
    if (ACTIVE_ASSIGNMENT.has(a.status) || shift.status === 'filled') {
      return shift;
    }
  }
  return null;
}

/** One-line summary for dashboard card */
export function activeShiftSummaryLine(shift) {
  if (!shift) return '';
  const parts = [];
  if (shift.shift_start) {
    parts.push(formatShiftDateTime(shift.shift_start, { dateStyle: 'medium', timeStyle: 'short' }));
  }
  const area = formatAreaDisplaySync(shiftAreaSource(shift));
  if (area) parts.push(area);
  return parts.join(' · ') || formatRoleLabel(shift.role_required);
}

/** Second line: role + status + urgency */
export function activeShiftMetaLine(shift) {
  if (!shift) return '';
  const statusLabel = isApplicationPending(shift)
    ? APPLICATION_STATUS_LABEL.applied
    : nurseAssignmentStatusLabel(shift.assignment?.status);
  const parts = [
    formatRoleLabel(shift.role_required),
    statusLabel,
    urgencyLabel(shift.urgency),
  ].filter(Boolean);
  return parts.join(' · ');
}
