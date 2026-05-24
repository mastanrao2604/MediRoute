/**
 * Authoritative dispatch state reconciliation — DB wins over WebSocket memory.
 */
import api from '../api/axios';
import { mlog, mlogError } from './mobileLogger';
import { filterJobSeekerOffers } from './shiftVisibility';

export const RECONCILE_EVENT = 'mr-dispatch-reconcile';

let reconcileInFlight = null;

/** Request backend truth and broadcast reconcile to listeners. */
export async function triggerDispatchReconcile(trigger = 'manual') {
  if (reconcileInFlight) return reconcileInFlight;
  reconcileInFlight = (async () => {
  window.dispatchEvent(
    new CustomEvent(RECONCILE_EVENT, { detail: { trigger, phase: 'start' } }),
  );
  try {
    const res = await api.get('/dispatch/reconcile');
    const payload = { ...res.data, trigger };
    window.dispatchEvent(
      new CustomEvent(RECONCILE_EVENT, { detail: { trigger, phase: 'done', payload } }),
    );
    mlog('dispatch', 'reconcile_ok', {
      trigger,
      role: payload.role,
      offers: payload.pending_offers?.length ?? 0,
      clear: payload.clear_offer_shift_ids?.length ?? 0,
      terminal: payload.terminal_shifts?.length ?? 0,
      active_shift_id: payload.active_shift_id ?? null,
    });
    return payload;
  } catch (err) {
    mlogError('dispatch', 'reconcile_fail', err, { trigger });
    window.dispatchEvent(
      new CustomEvent(RECONCILE_EVENT, { detail: { trigger, phase: 'error', err } }),
    );
    throw err;
  }
  })();
  try {
    return await reconcileInFlight;
  } finally {
    reconcileInFlight = null;
  }
}

/** Apply nurse reconcile payload to offer modal state. Returns true if offer UI changed. */
export function applyNurseOfferReconcile(
  payload,
  { currentOffer, minimizedOffer, clearShiftOfferUi },
) {
  if (!payload || payload.role === 'recruiter') {
    return { currentOffer, minimizedOffer, changed: false };
  }

  const pendingOffers = filterJobSeekerOffers(payload.pending_offers || []);
  const validOfferIds = new Set((payload.valid_offer_ids || []).map(Number));
  const clearIds = new Set((payload.clear_offer_shift_ids || []).map(Number));
  for (const t of payload.terminal_shifts || []) {
    if (t?.shift_id != null) clearIds.add(Number(t.shift_id));
  }

  for (const sid of clearIds) {
    clearShiftOfferUi(sid);
  }

  const offerStillValid = (offer) => {
    if (!offer) return false;
    const oid = Number(offer.offer_id);
    const sid = Number(offer.shift_id);
    if (clearIds.has(sid)) return false;
    if (validOfferIds.size > 0 && !validOfferIds.has(oid)) return false;
    if (payload.active_shift_id != null && sid === Number(payload.active_shift_id)) {
      return false;
    }
    return pendingOffers.some((o) => Number(o.offer_id) === oid);
  };

  let changed = clearIds.size > 0;
  let nextCurrent = currentOffer;
  let nextMinimized = minimizedOffer;

  if (nextCurrent && !offerStillValid(nextCurrent)) {
    nextCurrent = null;
    changed = true;
  }
  if (nextMinimized && !offerStillValid(nextMinimized)) {
    nextMinimized = null;
    changed = true;
  }

  if (!nextCurrent && pendingOffers.length > 0) {
    const top = pendingOffers[0];
    nextCurrent = { type: 'dispatch_offer', ...top, _receivedAt: Date.now() };
    nextMinimized = null;
    changed = true;
  }

  return { currentOffer: nextCurrent, minimizedOffer: nextMinimized, changed };
}

/** Fan-out refresh events after reconcile so dashboards reload from DB. */
export function notifyReconcileRefresh(payload) {
  if (!payload) return;
  const clearIds = new Set((payload.clear_offer_shift_ids || []).map(Number));
  for (const t of payload.terminal_shifts || []) {
    if (t?.shift_id != null) clearIds.add(Number(t.shift_id));
  }
  if (payload.role === 'recruiter') {
    window.dispatchEvent(new CustomEvent('mr-recruiter-shifts-refresh'));
    for (const sid of clearIds) {
      window.dispatchEvent(
        new CustomEvent('mr-jobs-shift-removed', { detail: { shiftId: sid } }),
      );
    }
    return;
  }
  window.dispatchEvent(new CustomEvent('mr-nurse-active-shift-refresh'));
  window.dispatchEvent(new CustomEvent('mr-jobs-shifts-refresh'));
  for (const sid of clearIds) {
    window.dispatchEvent(
      new CustomEvent('mr-jobs-shift-removed', { detail: { shiftId: sid } }),
    );
  }
}

let networkListenersInstalled = false;

/** Reconcile when browser/Capacitor regains network (airplane mode recovery). */
export function installNetworkReconnectListeners() {
  if (networkListenersInstalled || typeof window === 'undefined') return;
  networkListenersInstalled = true;
  window.addEventListener('online', () => {
    triggerDispatchReconcile('network_online').catch(() => {});
  });
  mlog('dispatch', 'network_reconcile_listeners_ok', {});
}
