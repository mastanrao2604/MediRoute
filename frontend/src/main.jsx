import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import * as Sentry from '@sentry/react'
import { Capacitor } from '@capacitor/core'
import './index.css'
import App from './App.jsx'

// Sentry is only active in production and only when VITE_SENTRY_DSN is set.
if (import.meta.env.VITE_SENTRY_DSN) {
  Sentry.init({
    dsn: import.meta.env.VITE_SENTRY_DSN,
    tracesSampleRate: 0.1,   // 10 % of navigations traced
    environment: import.meta.env.MODE,
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
