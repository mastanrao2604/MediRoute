/** Persist pilot/dev OTP for Verify screen autofill (Capacitor may drop navigation state). */

export function normalizeDevOtp(val) {
  if (val == null || val === '') return '';
  return String(val).replace(/\D/g, '').slice(0, 6);
}

/** Save OTP digits for the verify screen; returns normalized value or ''. */
export function stashDevOtp(devOtp) {
  const normalized = normalizeDevOtp(devOtp);
  if (!normalized) return '';
  try {
    sessionStorage.setItem('mr_dev_otp_pending', normalized);
  } catch {
    /* quota / private mode */
  }
  return normalized;
}

/** Read and clear stashed dev OTP (one-time consume). */
export function consumeStashedDevOtp() {
  try {
    const stored = normalizeDevOtp(sessionStorage.getItem('mr_dev_otp_pending'));
    if (stored) sessionStorage.removeItem('mr_dev_otp_pending');
    return stored;
  } catch {
    return '';
  }
}
