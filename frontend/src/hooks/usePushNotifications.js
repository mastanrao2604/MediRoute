/**
 * usePushNotifications — FCM token registration + notification routing (Capacitor Android).
 *
 * Responsibilities:
 *  1. Request Android notification permission (POST_NOTIFICATIONS, Android 13+)
 *  2. Create high-priority "dispatch" notification channel (Android 8+, IMPORTANCE_HIGH)
 *  3. Register for FCM, upload token to backend via PUT /device/token
 *  4. Handle token refresh (tokens can rotate — always re-register)
 *  5. Foreground push delivery: route dispatch_offer data to onDispatchOffer
 *     (WS is primary for foreground; this covers the edge case where WS was
 *      momentarily disconnected when the offer arrived)
 *  6. Notification tap routing for background + killed-app:
 *     Fetches current offer from GET /dispatch/offers/pending to:
 *      a) verify the offer is still pending (not expired/taken)
 *      b) get accurate expires_in_sec from the server
 *
 * Deduplication:
 *  Handled by DispatchManager in App.jsx — setCurrentOffer checks prev?.offer_id.
 *  This hook does NOT deduplicate; it simply passes the offer to onDispatchOffer.
 *
 * No-op when:
 *  - Not running in Capacitor native (browser / PWA build)
 *  - User not authenticated
 *  - Notification permission denied
 *  - @capacitor/push-notifications plugin throws 'not implemented' (dev build)
 */
import { useEffect, useRef } from 'react';
import { Capacitor } from '@capacitor/core';
import api from '../api/axios';

const IS_NATIVE = Capacitor.isNativePlatform();

// Must match DISPATCH_CHANNEL_ID in mediroute-backend/app/utils/fcm.py
const DISPATCH_CHANNEL_ID = 'dispatch';

export function usePushNotifications(user, token, onDispatchOffer) {
  const registeredTokenRef = useRef(null);

  useEffect(() => {
    if (!IS_NATIVE || !user?.id || !token) return;

    let cleanupFns = [];
    let cancelled = false;

    async function setup() {
      let PushNotifications;
      try {
        ({ PushNotifications } = await import('@capacitor/push-notifications'));
      } catch {
        // Plugin not available in this build — silent no-op
        return;
      }
      if (cancelled) return;

      // ── 1. Request notification permission ─────────────────────────────────
      // On Android 13+ (API 33+) this shows the OS permission dialog.
      // On Android 12 and below, permission is granted automatically.
      let permResult;
      try {
        permResult = await PushNotifications.requestPermissions();
      } catch (e) {
        // Gracefully handle 'not implemented' in web/dev builds
        if (e?.message?.includes('not implemented')) return;
        throw e;
      }
      if (permResult.receive !== 'granted') {
        console.warn('[FCM] Notification permission denied — push delivery disabled');
        return;
      }
      if (cancelled) return;

      // ── 2. Create high-priority dispatch notification channel (Android 8+) ─
      // Without a channel, notifications default to low importance on Android 8+.
      // IMPORTANCE_HIGH (5) = heads-up banner + sound + vibration even in Doze.
      if (Capacitor.getPlatform() === 'android') {
        try {
          await PushNotifications.createChannel({
            id: DISPATCH_CHANNEL_ID,
            name: 'Dispatch Offers',
            description: 'Urgent shift dispatch notifications — tap to respond',
            importance: 5,          // IMPORTANCE_HIGH
            visibility: 1,          // VISIBILITY_PUBLIC — show on lock screen
            vibration: true,
            sound: 'default',
            lights: true,
            lightColor: '#FF3B30',  // Red — reinforces urgency
          });
        } catch (e) {
          // createChannel may throw on very old devices or if already exists — safe to ignore
          console.debug('[FCM] createChannel:', e?.message);
        }
      }

      // ── 3. Register for FCM ────────────────────────────────────────────────
      // Triggers 'registration' event with the FCM token.
      await PushNotifications.register();

      // ── 4. Handle token delivered / refreshed ──────────────────────────────
      // FCM tokens can rotate (device reset, app reinstall, 60-day expiry).
      // Always re-register to keep the backend token current.
      const regListener = await PushNotifications.addListener('registration', async (data) => {
        const fcmToken = data.value;
        if (!fcmToken) return;

        // Skip if we already registered this exact token in this session
        if (fcmToken === registeredTokenRef.current) return;

        try {
          await api.put('/device/token', { fcm_token: fcmToken, platform: 'android' });
          registeredTokenRef.current = fcmToken;
          console.debug('[FCM] Token registered with backend (prefix:', fcmToken.slice(0, 12), ')');
        } catch (err) {
          // Non-critical — WS dispatch still works without a registered token
          console.error('[FCM] Token registration failed:', err?.response?.data?.detail || err.message);
        }
      });
      cleanupFns.push(() => regListener.remove());

      // Registration error (e.g. no google-services.json in debug build)
      const errListener = await PushNotifications.addListener('registrationError', (err) => {
        console.error('[FCM] Registration error:', err.error);
      });
      cleanupFns.push(() => errListener.remove());

      // ── 5. Foreground push notification received ───────────────────────────
      // When the app is in the foreground, Android suppresses the heads-up banner
      // by default. WS handles foreground delivery. This listener covers the edge
      // case where the WS was disconnected exactly when the offer arrived.
      // Deduplication by offer_id happens in DispatchManager.
      const fgListener = await PushNotifications.addListener(
        'pushNotificationReceived',
        (notification) => {
          const d = notification?.data ?? {};
          if (d.type !== 'dispatch_offer' || !d.offer_id) return;
          if (typeof onDispatchOffer !== 'function') return;

          onDispatchOffer({
            type: 'dispatch_offer',
            offer_id: Number(d.offer_id),
            shift_id: Number(d.shift_id),
            urgency: d.urgency || 'standard',
            expires_in_sec: Number(d.expires_in_sec) || 30,
            // Full shift details come from the WS payload or pending-offers fetch
          });
        },
      );
      cleanupFns.push(() => fgListener.remove());

      // ── 6. Notification tap routing (background + killed-app) ──────────────
      // BACKGROUND: app is alive but in background — this fires immediately on tap.
      // KILLED: Capacitor queues the event and fires it when JS finishes loading.
      //
      // We always fetch from the server to:
      //  a) Validate the offer is still pending (not expired/taken)
      //  b) Get accurate expires_in_sec (time-to-expiry from server clock)
      //
      // Server is the source of truth for offer state.
      const tapListener = await PushNotifications.addListener(
        'pushNotificationActionPerformed',
        async (action) => {
          const d = action?.notification?.data ?? {};
          if (d.type !== 'dispatch_offer') return;
          if (typeof onDispatchOffer !== 'function') return;

          const targetOfferId = d.offer_id ? Number(d.offer_id) : null;

          try {
            const res = await api.get('/dispatch/offers/pending');
            const offers = res.data?.offers ?? [];

            // Prefer the specific tapped offer; fall back to most urgent pending offer
            const match = targetOfferId
              ? offers.find(o => o.offer_id === targetOfferId) ?? offers[0]
              : offers[0];

            if (match) {
              onDispatchOffer({ type: 'dispatch_offer', ...match });
            }
            // If no match: offer expired or was taken by another nurse — show nothing
          } catch (err) {
            console.warn('[FCM] Tap recovery failed:', err.message);
          }
        },
      );
      cleanupFns.push(() => tapListener.remove());
    }

    setup().catch(err => {
      if (!err?.message?.includes('not implemented')) {
        console.error('[FCM] Setup failed:', err);
      }
    });

    return () => {
      cancelled = true;
      cleanupFns.forEach(fn => { try { fn(); } catch {} });
    };
  }, [user?.id, token]); // Re-run on user/token change (login/logout)
}
