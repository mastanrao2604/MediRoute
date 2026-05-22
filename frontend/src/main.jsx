import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import * as Sentry from '@sentry/react'
import { Capacitor } from '@capacitor/core'
import './debugLogBootstrap.js'

// PWA service workers break Capacitor WebView XHR to Render (ERR_NETWORK). Unregister on native.
if (Capacitor.isNativePlatform() && typeof navigator !== 'undefined' && navigator.serviceWorker) {
  navigator.serviceWorker.getRegistrations().then((regs) => {
    if (regs.length) {
      Promise.all(regs.map((r) => r.unregister())).catch(() => {});
    }
  });
}
import './index.css'
import App from './App.jsx'

// Sentry is only active when VITE_SENTRY_DSN is set (absent in dev / CI).
if (import.meta.env.VITE_SENTRY_DSN) {
  Sentry.init({
    dsn: import.meta.env.VITE_SENTRY_DSN,
    integrations: [Sentry.browserTracingIntegration()],
    tracesSampleRate: 0.1,            // 10 % of navigations traced
    environment: import.meta.env.MODE,
    release: import.meta.env.VITE_APP_VERSION || 'unknown',
    sendDefaultPii: false,            // never send cookies, auth headers, or IPs
    // Tag every event with the runtime platform (web / android / ios)
    initialScope: (scope) => {
      scope.setTag('app.platform', Capacitor.getPlatform())
      return scope
    },
    beforeSend(event) {
      // Belt-and-suspenders PII scrub — strip tokens/OTPs from any captured request body
      try {
        const body = event?.request?.data
        if (body && typeof body === 'object') {
          const REDACT = ['otp', 'password', 'token', 'phone', 'authorization', 'auth_key']
          for (const key of Object.keys(body)) {
            if (REDACT.includes(key.toLowerCase())) body[key] = '[REDACTED]'
          }
        }
      } catch { /* never crash the error reporter */ }
      return event
    },
  })
}

// ── Android safe-area-inset-bottom polyfill ─────────────────────────────────
// Capacitor 6+ draws the WebView edge-to-edge behind the Android system
// navigation bar. env(safe-area-inset-bottom) should report the bar height,
// but some OEM WebViews (Samsung, Xiaomi, Oppo, etc.) return 0 even when the
// bar is present. We detect this and set --sab-extra as a CSS variable
// fallback used by the bottom nav and main content padding.
if (Capacitor.getPlatform() === 'android') {
  const probe = document.createElement('div');
  probe.style.cssText =
    'position:fixed;bottom:0;left:-9999px;width:1px;height:1px;' +
    'padding-bottom:env(safe-area-inset-bottom,0px);' +
    'pointer-events:none;opacity:0;z-index:-1;';
  document.body.appendChild(probe);
  // Double rAF: first frame starts layout, second reads env() after it resolves.
  requestAnimationFrame(() => requestAnimationFrame(() => {
    const inset = parseFloat(window.getComputedStyle(probe).paddingBottom) || 0;
    if (probe.parentNode) probe.parentNode.removeChild(probe);
    if (inset === 0) {
      // env() is not working on this device. Apply a safe minimum that covers:
      //   • 3-button navigation bar: ~48dp
      //   • Gesture navigation indicator: ~20–24dp
      // 56px covers both on most devices in production.
      document.documentElement.style.setProperty('--sab-extra', '56px');
    }
  }));
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
