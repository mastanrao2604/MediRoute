/**
 * usePushNotifications — FCM token registration + notification routing (Capacitor Android).
 *
 * WS is primary in foreground; FCM covers background / killed / stale-socket cases.
 * Client dedupes WS+FCM via DispatchManager (offer_id) and recent-event keys here.
 */
import { useEffect, useRef, useCallback } from 'react';
import { Capacitor } from '@capacitor/core';
import api from '../api/axios';
import { mlog } from '../utils/mobileLogger';

const IS_NATIVE = Capacitor.isNativePlatform();
const DISPATCH_CHANNEL_ID = 'dispatch';

const OFFER_TYPES = new Set(['dispatch_offer']);
const STAFFING_TYPES = new Set([
  'assignment_confirmed',
  'shift_cancelled',
  'offer_revoked',
  'application_submitted',
]);

function eventKey(d) {
  const type = d?.type || '';
  const shiftId = d?.shift_id != null ? String(d.shift_id) : '';
  const offerId = d?.offer_id != null ? String(d.offer_id) : '';
  return `${type}:${shiftId}:${offerId}`;
}

function normalizePushData(d) {
  if (!d?.type) return null;
  const msg = { ...d };
  if (msg.shift_id != null) msg.shift_id = Number(msg.shift_id);
  if (msg.offer_id != null) msg.offer_id = Number(msg.offer_id);
  if (msg.assignment_id != null) msg.assignment_id = Number(msg.assignment_id);
  if (msg.expires_in_sec != null) msg.expires_in_sec = Number(msg.expires_in_sec);
  return msg;
}

export function usePushNotifications(user, token, onPushMessage) {
  const registeredTokenRef = useRef(null);
  const recentEventsRef = useRef(new Map());

  const shouldEmit = useCallback((msg) => {
    if (!msg?.type) return false;
    const key = eventKey(msg);
    const now = Date.now();
    const last = recentEventsRef.current.get(key);
    if (last && now - last < 8000) return false;
    recentEventsRef.current.set(key, now);
    if (recentEventsRef.current.size > 40) {
      for (const [k, ts] of recentEventsRef.current) {
        if (now - ts > 60000) recentEventsRef.current.delete(k);
      }
    }
    return true;
  }, []);

  const emitPush = useCallback((raw, source) => {
    if (typeof onPushMessage !== 'function') return;
    const msg = normalizePushData(raw);
    if (!msg) return;
    if (!shouldEmit(msg)) {
      mlog('notification', 'push_deduped', { type: msg.type, source });
      return;
    }
    mlog('notification', `push_${source}`, {
      type: msg.type,
      shift_id: msg.shift_id,
      offer_id: msg.offer_id,
    });
    onPushMessage(msg);
  }, [onPushMessage, shouldEmit]);

  const recoverFromServer = useCallback(async (d) => {
    const type = d?.type;
    if (OFFER_TYPES.has(type)) {
      const targetOfferId = d.offer_id ? Number(d.offer_id) : null;
      const res = await api.get('/dispatch/offers/pending');
      const offers = res.data?.offers ?? [];
      const match = targetOfferId
        ? offers.find((o) => o.offer_id === targetOfferId) ?? offers[0]
        : offers[0];
      if (match) {
        emitPush({ type: 'dispatch_offer', ...match }, 'tap_recovered');
      }
      return;
    }
    if (STAFFING_TYPES.has(type)) {
      emitPush(
        {
          type,
          shift_id: d.shift_id,
          message: d.message,
          hospital_name: d.hospital_name,
          shift_start: d.shift_start,
          lifecycle_stage: d.lifecycle_stage,
          application_status: d.application_status,
          assignment_id: d.assignment_id,
        },
        'tap_recovered',
      );
      window.dispatchEvent(new CustomEvent('mr-nurse-active-shift-refresh'));
    }
  }, [emitPush]);

  useEffect(() => {
    if (!IS_NATIVE || !user?.id || !token) return;

    let cleanupFns = [];
    let cancelled = false;

    async function setup() {
      let PushNotifications;
      try {
        ({ PushNotifications } = await import('@capacitor/push-notifications'));
      } catch {
        return;
      }
      if (cancelled) return;

      let permResult;
      try {
        permResult = await PushNotifications.requestPermissions();
      } catch (e) {
        if (e?.message?.includes('not implemented')) return;
        throw e;
      }
      if (permResult.receive !== 'granted') {
        mlog('notification', 'permission_denied', {});
        return;
      }
      if (cancelled) return;

      if (Capacitor.getPlatform() === 'android') {
        try {
          await PushNotifications.createChannel({
            id: DISPATCH_CHANNEL_ID,
            name: 'Dispatch Offers',
            description: 'Urgent shift dispatch and staffing updates',
            importance: 5,
            visibility: 1,
            vibration: true,
            sound: 'default',
            lights: true,
            lightColor: '#FF3B30',
          });
        } catch (e) {
          console.debug('[FCM] createChannel:', e?.message);
        }
      }

      await PushNotifications.register();

      const regListener = await PushNotifications.addListener('registration', async (data) => {
        const fcmToken = data.value;
        if (!fcmToken || fcmToken === registeredTokenRef.current) return;
        try {
          await api.put('/devices/token', { fcm_token: fcmToken, platform: 'android' });
          registeredTokenRef.current = fcmToken;
          mlog('notification', 'fcm_registered', { token_len: fcmToken.length });
        } catch (err) {
          const status = err?.response?.status ?? null;
          mlog('notification', 'fcm_token_upload_fail', {
            status,
            msg: String(err?.response?.data?.detail || err?.message || err).slice(0, 120),
          });
        }
      });
      cleanupFns.push(() => regListener.remove());

      const errListener = await PushNotifications.addListener('registrationError', (err) => {
        const msg = String(err?.error || err?.message || err || '');
        const placeholderConfig = /valid API key|API key is required/i.test(msg);
        mlog('notification', 'fcm_registration_error', {
          msg: msg.slice(0, 160),
          hint: placeholderConfig
            ? 'Replace frontend/android/app/google-services.json with Firebase Console download (mediroute-app-dev, com.mediroute.app)'
            : undefined,
        });
      });
      cleanupFns.push(() => errListener.remove());

      const fgListener = await PushNotifications.addListener(
        'pushNotificationReceived',
        (notification) => {
          const d = notification?.data ?? {};
          if (OFFER_TYPES.has(d.type)) {
            emitPush(d, 'foreground');
            return;
          }
          if (STAFFING_TYPES.has(d.type)) {
            emitPush(
              { ...d, message: d.message || notification?.body },
              'foreground',
            );
            window.dispatchEvent(new CustomEvent('mr-nurse-active-shift-refresh'));
          }
        },
      );
      cleanupFns.push(() => fgListener.remove());

      const tapListener = await PushNotifications.addListener(
        'pushNotificationActionPerformed',
        async (action) => {
          const d = action?.notification?.data ?? {};
          if (!d.type) return;
          try {
            await recoverFromServer({ ...d, message: d.message || action?.notification?.body });
          } catch (err) {
            mlog('notification', 'push_tap_recover_failed', { msg: (err?.message || '').slice(0, 120) });
          }
        },
      );
      cleanupFns.push(() => tapListener.remove());

      try {
        const { App } = await import('@capacitor/app');
        const resumeListener = await App.addListener('appStateChange', ({ isActive }) => {
          if (!isActive) return;
          registeredTokenRef.current = null;
          PushNotifications.register().catch(() => {});
          window.dispatchEvent(new CustomEvent('mr-nurse-active-shift-refresh'));
        });
        cleanupFns.push(() => resumeListener.remove());
      } catch {
        /* browser */
      }
    }

    setup().catch((err) => {
      if (!err?.message?.includes('not implemented')) {
        mlog('notification', 'setup_failed', { msg: String(err?.message || err).slice(0, 120) });
      }
    });

    return () => {
      cancelled = true;
      cleanupFns.forEach((fn) => { try { fn(); } catch {} });
    };
  }, [user?.id, token, emitPush, recoverFromServer]);

  useEffect(() => {
    registeredTokenRef.current = null;
  }, [user?.id]);
}
