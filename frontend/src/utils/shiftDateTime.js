/** India shift times: API stores UTC; UI shows and edits in IST. */

export const SHIFT_TZ = 'Asia/Kolkata';

/**
 * Parse API datetime. Naive strings (no Z) are treated as UTC — not local wall clock.
 */
export function parseShiftDateTime(iso) {
  if (!iso) return null;
  const s = String(iso).trim();
  if (!s) return null;
  if (/[zZ]$/.test(s) || /[+-]\d{2}:?\d{2}$/.test(s)) {
    const d = new Date(s);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  const d = new Date(`${s.replace(/\.\d+$/, '')}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Time only in IST (e.g. nurse dashboard offer rows). */
export function formatShiftTime(iso) {
  const d = parseShiftDateTime(iso);
  if (!d) return '—';
  return d.toLocaleString('en-IN', {
    timeZone: SHIFT_TZ,
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  });
}

/** Display for recruiters/nurses (IST). */
export function formatShiftDateTime(iso, options = {}) {
  const d = parseShiftDateTime(iso);
  if (!d) return '—';

  // Intl forbids mixing dateStyle/timeStyle with weekday/month/hour/etc.
  if (options.dateStyle != null || options.timeStyle != null) {
    const { dateStyle, timeStyle, ...rest } = options;
    return d.toLocaleString('en-IN', {
      timeZone: SHIFT_TZ,
      ...(dateStyle != null ? { dateStyle } : {}),
      ...(timeStyle != null ? { timeStyle } : {}),
      ...rest,
    });
  }

  return d.toLocaleString('en-IN', {
    timeZone: SHIFT_TZ,
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
    ...options,
  });
}

/** Value for <input type="datetime-local" /> in IST wall clock. */
export function toDatetimeLocalValue(iso) {
  const d = parseShiftDateTime(iso);
  if (!d) return '';
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: SHIFT_TZ,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(d);
  const pick = (type) => parts.find((p) => p.type === type)?.value ?? '';
  return `${pick('year')}-${pick('month')}-${pick('day')}T${pick('hour')}:${pick('minute')}`;
}

/**
 * datetime-local value → UTC ISO for API.
 * Uses IST when the string has no offset (MediRoute default region).
 */
export function datetimeLocalToUtcIso(localValue) {
  if (!localValue) return null;
  const normalized = localValue.length === 16 ? `${localValue}:00` : localValue;
  const d = parseShiftDateTime(`${normalized}+05:30`);
  return d ? d.toISOString() : null;
}

export function isPastShiftStartUtc(iso) {
  const d = parseShiftDateTime(iso);
  if (!d) return false;
  return d.getTime() <= Date.now();
}

export function isBeforeShiftStartUtc(iso) {
  const d = parseShiftDateTime(iso);
  if (!d) return false;
  return d.getTime() > Date.now();
}

/** Default datetime-local value for Post Shift (IST wall clock). */
export function nowDatetimeLocalPlusMinutes(minutes) {
  const target = Date.now() + minutes * 60 * 1000;
  return toDatetimeLocalValue(new Date(target).toISOString());
}
