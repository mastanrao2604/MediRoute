/**
 * shareJob.js — Native job sharing utility for MediRoute.
 *
 * Uses @capacitor/share for native Android share sheet (best UX).
 * Falls back to navigator.share (Web Share API) on mobile browsers.
 * Last resort: copies text to clipboard.
 *
 * Share URL opens the social landing page which shows job details
 * with OG meta tags for rich WhatsApp/LinkedIn/Telegram previews.
 * When the link is tapped on Android with the app installed,
 * Android App Links intercept it and open the job inside the app.
 */

const BACKEND_URL = 'https://mediroute-8az0.onrender.com';

/**
 * Build a share URL for a given job ID.
 * @param {number} jobId
 * @returns {string}
 */
export function buildShareUrl(jobId) {
  return `${BACKEND_URL}/share/job/${jobId}`;
}

/**
 * Build a high-conversion share message for WhatsApp / Telegram.
 * @param {object} job — job object from the API
 * @returns {string}
 */
function buildShareText(job) {
  const role = (job.role_required || '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());

  const locationParts = [job.location, job.country].filter(Boolean);
  const location = locationParts.join(', ');

  const lines = [
    job.hospital_name
      ? `🏥 *${job.hospital_name} is Hiring!*`
      : `🏥 *Hiring on MediRoute!*`,
    ``,
    `👨‍⚕️ ${job.title}`,
    location          ? `📍 ${location}`          : null,
    job.salary        ? `💰 ${job.salary}`         : null,
    role              ? `👤 ${role}`               : null,
  ].filter((l) => l !== null);

  return lines.join('\n');
}

/**
 * Trigger native share sheet with job details and link.
 * Handles all platform fallbacks gracefully.
 *
 * @param {object} job — job object from the API
 * @returns {Promise<void>}
 */
export async function shareJob(job) {
  const url = buildShareUrl(job.id);
  const text = buildShareText(job);
  const title = job.title;

  // 1. Try Capacitor native share sheet (Android APK — best UX)
  try {
    const { Share } = await import('@capacitor/share');
    const canShare = await Share.canShare();
    if (canShare?.value) {
      await Share.share({ title, text, url, dialogTitle: 'Share this job' });
      return;
    }
  } catch {
    // Not in Capacitor context or plugin unavailable — continue to fallback
  }

  // 2. Try Web Share API (mobile browsers that support it)
  if (navigator.share) {
    try {
      await navigator.share({ title, text: `${text}\n\n${url}`, url });
      return;
    } catch (err) {
      // User cancelled — not an error
      if (err?.name === 'AbortError') return;
    }
  }

  // 3. Last resort: copy full message to clipboard
  try {
    await navigator.clipboard.writeText(`${text}\n\n${url}`);
  } catch {
    // Clipboard API blocked — silently ignore
  }
}
