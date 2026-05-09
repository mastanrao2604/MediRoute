import { useRegisterSW } from 'virtual:pwa-register/react';

/**
 * Shows a small banner when a new version of the app is available.
 * The user can tap "Update" to reload and activate the new service worker.
 */
export default function UpdatePrompt() {
  const {
    needRefresh: [needRefresh, setNeedRefresh],
    updateServiceWorker,
  } = useRegisterSW({
    onRegistered(r) {
      // Periodically check for updates every 60 minutes
      r && setInterval(() => r.update(), 60 * 60 * 1000);
    },
  });

  if (!needRefresh) return null;

  return (
    <div className="fixed top-4 left-4 right-4 lg:left-auto lg:right-4 lg:w-80 bg-indigo-600 text-white rounded-2xl shadow-xl p-4 flex items-center gap-3 z-50">
      <svg className="w-5 h-5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
      </svg>
      <p className="flex-1 text-sm font-medium">Update available</p>
      <div className="flex gap-2">
        <button
          onClick={() => setNeedRefresh(false)}
          className="text-xs text-indigo-200 hover:text-white px-2 py-1 transition-colors"
        >
          Later
        </button>
        <button
          onClick={() => updateServiceWorker(true)}
          className="text-xs font-semibold bg-white text-indigo-600 px-3 py-1.5 rounded-lg hover:bg-indigo-50 transition-colors"
        >
          Update
        </button>
      </div>
    </div>
  );
}
