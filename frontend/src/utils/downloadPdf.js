import { Capacitor } from '@capacitor/core';

/**
 * Download or share a PDF blob, adapting to the runtime environment:
 *  - Native Android/iOS (Capacitor): writes to device cache dir, opens the
 *    native Android share sheet so the user can save, open, or share the file.
 *  - Web with File Share support (mobile Chrome on Android): uses the Web Share API.
 *  - Desktop / fallback: creates an anchor element with blob URL and auto-clicks.
 *
 * Throws on unexpected errors; callers should catch and show a user message.
 * AbortError (user dismissed share sheet) is silently swallowed here.
 */
export async function downloadPDF(blob, fileName) {
  // ── 1. Capacitor native (Android / iOS) ────────────────────────────────
  if (Capacitor.isNativePlatform()) {
    const { Filesystem, Directory } = await import('@capacitor/filesystem');
    const { Share } = await import('@capacitor/share');

    const base64 = await _blobToBase64(blob);
    const { uri } = await Filesystem.writeFile({
      path: fileName,
      data: base64,
      directory: Directory.Cache,
      recursive: true,
    });

    try {
      await Share.share({
        title: fileName,
        url: uri,
        dialogTitle: 'Save or share your resume',
      });
    } catch (shareErr) {
      // User dismissed the share sheet — not an error
      if (shareErr?.name === 'AbortError' || shareErr?.message?.includes('cancel')) return;
      throw shareErr;
    }
    return;
  }

  // ── 2. Web with File Share API (mobile Chrome on Android via browser) ──
  if (typeof navigator.canShare === 'function') {
    const file = new File([blob], fileName, { type: 'application/pdf' });
    if (navigator.canShare({ files: [file] })) {
      try {
        await navigator.share({ files: [file], title: 'My Resume' });
        return;
      } catch (shareErr) {
        if (shareErr?.name === 'AbortError') return; // user dismissed
        // Fall through to anchor approach if share fails
      }
    }
  }

  // ── 3. Desktop / fallback: anchor + blob URL ───────────────────────────
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoke after 60 s so the browser has time to start the download
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function _blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      // result is "data:application/pdf;base64,XXXX" — strip the prefix
      const result = reader.result;
      resolve(typeof result === 'string' ? result.split(',')[1] : result);
    };
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}
