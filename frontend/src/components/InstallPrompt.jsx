import { useState, useEffect } from 'react';

/**
 * Shows a banner asking the user to install the app (PWA / Android).
 * The banner only appears when the browser fires `beforeinstallprompt`,
 * i.e. on Chrome / Edge on Android (and desktop). It is completely hidden
 * inside the Capacitor WebView (where the app is already "installed").
 */
export default function InstallPrompt() {
  const [deferredPrompt, setDeferredPrompt] = useState(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    // If running inside Capacitor, skip — it's already a native app
    if (window.Capacitor?.isNativePlatform?.()) return;

    function onBeforeInstallPrompt(e) {
      e.preventDefault();
      setDeferredPrompt(e);
      setVisible(true);
    }
    window.addEventListener('beforeinstallprompt', onBeforeInstallPrompt);
    return () => window.removeEventListener('beforeinstallprompt', onBeforeInstallPrompt);
  }, []);

  async function handleInstall() {
    if (!deferredPrompt) return;
    deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;
    if (outcome === 'accepted') {
      setDeferredPrompt(null);
    }
    setVisible(false);
  }

  if (!visible) return null;

  return (
    <div className="fixed bottom-20 lg:bottom-4 left-4 right-4 lg:left-auto lg:right-4 lg:w-80 bg-white border border-indigo-200 rounded-2xl shadow-xl p-4 flex items-center gap-3 z-50 animate-in slide-in-from-bottom-4">
      {/* Icon */}
      <div className="w-10 h-10 rounded-xl bg-indigo-600 flex items-center justify-center shrink-0">
        <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z" />
        </svg>
      </div>
      {/* Text */}
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-gray-900">Install MediRoute</p>
        <p className="text-xs text-gray-500 leading-snug">Add to home screen for quick access</p>
      </div>
      {/* Actions */}
      <div className="flex flex-col gap-1 shrink-0">
        <button
          onClick={handleInstall}
          className="text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-700 px-3 py-1.5 rounded-lg transition-colors"
        >
          Install
        </button>
        <button
          onClick={() => setVisible(false)}
          className="text-xs text-gray-400 hover:text-gray-600 px-3 py-1 transition-colors text-center"
        >
          Not now
        </button>
      </div>
    </div>
  );
}
