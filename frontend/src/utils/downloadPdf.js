import { Capacitor } from '@capacitor/core';

/**
 * Download a PDF blob, adapting to the runtime environment:
 *
 *  - Native Android/iOS (Capacitor):
 *      1. Writes to the public Downloads folder.
 *      2. Shows a system notification: "Resume Downloaded — Tap to open".
 *      3. Tapping the notification opens the PDF in the default viewer.
 *
 *  - Web / desktop fallback:
 *      Anchor + blob URL auto-click (standard browser download).
 *
 * Returns { savedTo: 'downloads' | 'documents' | 'browser' }
 */

// Listener registered once per app session — avoid accumulating duplicates.
let _notifListenerSetUp = false;

export async function downloadPDF(blob, fileName) {
  // ── 1. Capacitor native (Android / iOS) ────────────────────────────────
  if (Capacitor.isNativePlatform()) {
    const { Filesystem, Directory } = await import('@capacitor/filesystem');

    const base64 = await _blobToBase64(blob);

    let uri;
    let savedTo;

    try {
      const result = await Filesystem.writeFile({
        path: `Download/${fileName}`,
        data: base64,
        directory: Directory.ExternalStorage,
        recursive: true,
      });
      uri = result.uri;
      savedTo = 'downloads';
    } catch {
      // ExternalStorage unavailable — fall back to app Documents
      const result = await Filesystem.writeFile({
        path: fileName,
        data: base64,
        directory: Directory.Documents,
        recursive: true,
      });
      uri = result.uri;
      savedTo = 'documents';
    }

    // Show system notification — tap it to open the PDF
    await _showDownloadNotification(uri, fileName);

    return { savedTo, uri };
  }

  // ── 2. Web / desktop: anchor + blob URL ───────────────────────────────
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
  return { savedTo: 'browser' };
}

// ── View (preview) PDF ────────────────────────────────────────────────────────

/**
 * Open a PDF blob for immediate viewing (no download, no notification):
 *
 *  - Native Android/iOS: writes to the app Cache directory then opens in the
 *    device's default PDF viewer via FileOpener.
 *  - Web / desktop: opens a blob URL in a new browser tab (inline display).
 *
 * Returns { openedIn: 'viewer' | 'browser' }
 */
export async function viewPDF(blob) {
  if (Capacitor.isNativePlatform()) {
    const { Filesystem, Directory } = await import('@capacitor/filesystem');
    const { FileOpener } = await import('@capacitor-community/file-opener');

    const base64 = await _blobToBase64(blob);

    // Write to Cache (not Downloads) — temp file for viewing, no notification
    const result = await Filesystem.writeFile({
      path: 'preview_resume.pdf',
      data: base64,
      directory: Directory.Cache,
      recursive: true,
    });

    // Opens immediately in the device's default PDF viewer (Adobe, Google Docs, etc.)
    await FileOpener.open({ filePath: result.uri, contentType: 'application/pdf' });
    return { openedIn: 'viewer' };
  }

  // Web: open blob URL in new tab — browser renders it inline
  const url = URL.createObjectURL(blob);
  window.open(url, '_blank');
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
  return { openedIn: 'browser' };
}

// ── Notification ──────────────────────────────────────────────────────────────

async function _showDownloadNotification(uri, fileName) {
  try {
    const { LocalNotifications } = await import('@capacitor/local-notifications');
    const { FileOpener } = await import('@capacitor-community/file-opener');

    // Request permission (required on Android 13+ / API 33+)
    const perm = await LocalNotifications.requestPermissions();
    if (perm.display !== 'granted') return;

    // Create notification channel (Android requires this; no-op on iOS)
    await LocalNotifications.createChannel({
      id: 'mediroute_downloads',
      name: 'Downloads',
      description: 'Resume download notifications',
      importance: 4,  // HIGH — shows heads-up banner
      sound: 'default',
      vibration: true,
    });

    // Register tap listener once per app session
    if (!_notifListenerSetUp) {
      _notifListenerSetUp = true;
      LocalNotifications.addListener('localNotificationActionPerformed', async (event) => {
        const fileUri = event.notification?.extra?.fileUri;
        if (fileUri) {
          try {
            await FileOpener.open({ filePath: fileUri, contentType: 'application/pdf' });
          } catch { /* file may have been deleted — ignore */ }
        }
      });
    }

    const notifId = Date.now() % 2147483647; // 32-bit signed int limit
    await LocalNotifications.schedule({
      notifications: [{
        id: notifId,
        title: 'Resume Downloaded',
        body: `${fileName} saved to Downloads. Tap to open.`,
        channelId: 'mediroute_downloads',
        extra: { fileUri: uri },
        autoCancel: true,
      }],
    });
  } catch {
    // Notification is non-critical — file is already saved, silently skip
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function _blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      const result = reader.result;
      resolve(typeof result === 'string' ? result.split(',')[1] : result);
    };
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}
