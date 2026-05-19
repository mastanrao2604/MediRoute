/**
 * Enables mirrored debug logging early (before React mounts).
 * Append ?debugLog=1 to any URL once → persists mediroute_debug_log in localStorage.
 * Use ?debugLog=0 to turn off.
 */
try {
  const q = new URLSearchParams(window.location.search || '')
  if (q.get('debugLog') === '1') {
    window.localStorage.setItem('mediroute_debug_log', '1')
  }
  if (q.get('debugLog') === '0') {
    window.localStorage.removeItem('mediroute_debug_log')
  }
} catch {
  /* ignore */
}
