/**
 * phoneValidation.js — Centralized phone validation utility.
 *
 * Currently targets Indian mobile numbers.
 * Architecture is extensible: pass a `country` param in future for Germany/UAE.
 */

// Patterns that indicate fake/test numbers (all same digit, sequential, etc.)
const FAKE_PATTERNS = [
  /^(\d)\1{9}$/,      // 9999999999, 8888888888, 7777777777, etc.
  /^1234567890$/,
  /^0987654321$/,
  /^1111111111$/,
  /^2222222222$/,
  /^3333333333$/,
  /^4444444444$/,
  /^5555555555$/,
  /^0000000000$/,
  /^9876543210$/,
];

/**
 * Strip common country-code prefixes (+91, 91, leading 0) from raw input.
 * Returns the stripped string — may still be invalid.
 */
export function stripCountryCode(raw) {
  let v = raw.trim().replace(/\s+/g, '');
  if (v.startsWith('+91')) return v.slice(3);
  if (v.startsWith('91') && v.length === 12) return v.slice(2);
  if (v.startsWith('0') && v.length === 11) return v.slice(1);
  return v;
}

/**
 * Validate an Indian mobile number.
 *
 * Returns:
 *   { valid: true,  cleaned: '9876543210' }
 *   { valid: false, error: 'Human-readable error message' }
 */
export function validateIndianPhone(raw) {
  if (!raw || !raw.trim()) {
    return { valid: false, error: 'Phone number is required.' };
  }

  const cleaned = stripCountryCode(raw);

  if (!/^\d+$/.test(cleaned)) {
    return { valid: false, error: 'Use digits only — no spaces or symbols.' };
  }

  if (cleaned.length < 10) {
    const need = 10 - cleaned.length;
    return { valid: false, error: `${need} more digit${need === 1 ? '' : 's'} needed.` };
  }

  if (cleaned.length > 10) {
    return { valid: false, error: 'Phone number must be exactly 10 digits.' };
  }

  if (!/^[6-9]/.test(cleaned)) {
    return { valid: false, error: 'Number must start with 6, 7, 8, or 9.' };
  }

  for (const pattern of FAKE_PATTERNS) {
    if (pattern.test(cleaned)) {
      return { valid: false, error: 'Please enter a real mobile number.' };
    }
  }

  return { valid: true, cleaned };
}
