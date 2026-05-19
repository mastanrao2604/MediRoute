/**
 * mobileLogger — lean persistent log store for the Android debug APK.
 *
 * Storage: app internal data directory (always available, no permissions needed)
 *   Android path: /data/data/com.mediroute.app/files/mr-logs/app.log
 *
 * Pull on device (debug APK): scripts/android-pull-mr-logs.ps1 or:
 *   adb shell run-as com.mediroute.app cat files/mr-logs/app.log > app.log
 *   adb shell run-as com.mediroute.app cat files/mr-logs/app.log.1 > app.log.1
 *
 * Pilot helpers: scripts/android-debug-verify.ps1, android-debug-logcat-operational.ps1
 *
 * Format: NDJSON — one JSON object per line, newest entries at the bottom.
 * Rotation: when app.log exceeds ~200 KB it is archived as app.log.1 and
 *   a fresh app.log starts. Only one archive is kept.
 *
 * NEVER log: auth tokens, raw OTP values, passwords, phone numbers, patient data.
 * Safe to log: categories, event names, HTTP status codes, anonymised IDs.
 *
 * Enable adb logcat (debug) mirror for mlog: set VITE_DEBUG_LOG=true, use ?debugLog=1,
 * or localStorage mediroute_debug_log=1 — see isDebugLogMirrorEnabled().
 */
import { Capacitor } from '@capacitor/core';

const IS_NATIVE = Capacitor.isNativePlatform();

/**
 * When true, every mlog line is also emitted as console.debug so it shows in
 * adb logcat (Chromium WebView). Enable via:
 *   • VITE_DEBUG_LOG=true at build time, or
 *   • localStorage mediroute_debug_log=1 (see debugLogBootstrap.js / ?debugLog=1), or
 *   • import.meta.env.DEV (vite dev server)
 */
export function isDebugLogMirrorEnabled() {
  try {
    if (import.meta.env.VITE_DEBUG_LOG === 'false') return false;
    if (import.meta.env.VITE_DEBUG_LOG === 'true') return true;
    if (import.meta.env.DEV) return true;
    if (typeof window !== 'undefined' && window.localStorage?.getItem('mediroute_debug_log') === '1') {
      return true;
    }
  } catch {
    /* ignore */
  }
  return false;
}

/** Ad-hoc debug lines (same guard as mlog mirror). */
export function dlog(...args) {
  if (!isDebugLogMirrorEnabled()) return;
  console.debug('[MR]', ...args);
}
const LOG_DIR   = 'mr-logs';
const LOG_FILE  = `${LOG_DIR}/app.log`;
const LOG_ARCH  = `${LOG_DIR}/app.log.1`;
const MAX_CHARS = 200_000; // ~200 KB of UTF-8 text — rotate when exceeded

// Serial queue — prevents concurrent readFile/writeFile on the same file.
let _queue = Promise.resolve();

// Lazily resolved Filesystem module (avoids loading native bridge in browser).
let _fs = null;
async function _getFs() {
  if (_fs) return _fs;
  _fs = await import('@capacitor/filesystem');
  return _fs;
}

// One-shot directory creation — called once before the first write.
let _dirReady = null;
async function _ensureDir() {
  if (_dirReady) return _dirReady;
  const { Filesystem, Directory } = await _getFs();
  _dirReady = Filesystem.mkdir({
    path: LOG_DIR,
    directory: Directory.Data,
    recursive: true,
  }).catch(() => {}); // ignore "already exists"
  return _dirReady;
}

function _now() { return new Date().toISOString(); }

async function _doWrite(line) {
  const { Filesystem, Directory, Encoding } = await _getFs();

  await _ensureDir();

  const opts = { directory: Directory.Data, encoding: Encoding.UTF8 };

  // Read existing content (empty string on first write)
  let existing = '';
  try {
    const result = await Filesystem.readFile({ path: LOG_FILE, ...opts });
    existing = typeof result.data === 'string' ? result.data : '';
  } catch {
    // File doesn't exist yet — start fresh
  }

  // Rotate when the file grows too large
  if (existing.length > MAX_CHARS) {
    try {
      await Filesystem.writeFile({ path: LOG_ARCH, data: existing, ...opts });
    } catch (e) {
      console.warn('[mlog] archive write failed:', e?.message);
    }
    existing = '';
  }

  // Write updated content (existing + new line)
  await Filesystem.writeFile({ path: LOG_FILE, data: existing + line, ...opts });
}

/**
 * mlog(category, event, data?)
 *
 * category : 'auth' | 'otp' | 'websocket' | 'api' | 'dispatch' | 'lifecycle' | 'error'
 *            | 'availability' | 'notification'
 * event    : short snake_case string  e.g. 'otp_send_start'
 * data     : optional flat object — NO tokens, NO OTP values, NO passwords
 *
 * @example
 *   mlog('otp', 'send_success', { dev_mode: true });
 *   mlog('websocket', 'closed', { code: 1006, backoff_sec: 4 });
 */
export function mlog(category, event, data = {}) {
  if (isDebugLogMirrorEnabled()) {
    console.debug('[MR mlog]', category, event, data);
  }
  if (!IS_NATIVE) return;

  const entry = { ts: _now(), cat: category, ev: event, ...data };
  const line  = JSON.stringify(entry) + '\n';

  _queue = _queue
    .then(() => _doWrite(line))
    .catch((e) => {
      // Surface write errors in logcat so they're visible during device debugging
      console.error('[mlog] write error (cat=%s ev=%s):', category, event, e?.message ?? e);
    });
}

/**
 * mlogError — convenience wrapper for Error / axios error objects.
 * Logs message + HTTP status + code. Never logs response body or headers.
 */
export function mlogError(category, event, err, extra = {}) {
  mlog(category, event, {
    msg:    err?.message  || String(err),
    status: err?.response?.status ?? null,
    code:   err?.code     ?? null,
    ...extra,
  });
}

/**
 * readLogs — returns raw app.log content as a string (for share/copy UI).
 * Only works on the native device; returns a placeholder on web.
 */
export async function readLogs() {
  if (!IS_NATIVE) return '(logs only available on device)';
  try {
    const { Filesystem, Directory, Encoding } = await _getFs();
    const result = await Filesystem.readFile({
      path: LOG_FILE,
      directory: Directory.Data,
      encoding: Encoding.UTF8,
    });
    return typeof result.data === 'string' ? result.data : '(empty)';
  } catch (e) {
    return `(no log file yet: ${e?.message})`;
  }
}
