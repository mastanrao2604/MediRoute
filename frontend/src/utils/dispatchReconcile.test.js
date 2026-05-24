import { describe, it, expect, vi } from 'vitest';

vi.mock('@capacitor/core', () => ({
  Capacitor: { isNativePlatform: () => false },
}));

vi.mock('../api/axios', () => ({
  default: {
    get: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

import {
  applyNurseOfferReconcile,
  notifyReconcileRefresh,
} from './dispatchReconcile.js';

describe('applyNurseOfferReconcile', () => {
  const clearShiftOfferUi = vi.fn();

  it('clears stale offer when shift in clear_offer_shift_ids', () => {
    const current = { shift_id: 10, offer_id: 100, type: 'dispatch_offer' };
    const payload = {
      role: 'nurse',
      pending_offers: [],
      valid_offer_ids: [],
      clear_offer_shift_ids: [10],
      terminal_shifts: [],
    };
    const result = applyNurseOfferReconcile(payload, {
      currentOffer: current,
      minimizedOffer: null,
      clearShiftOfferUi,
    });
    expect(result.currentOffer).toBeNull();
    expect(result.changed).toBe(true);
    expect(clearShiftOfferUi).toHaveBeenCalledWith(10);
  });

  it('clears ghost offer on terminal shift without WS event', () => {
    const current = { shift_id: 20, offer_id: 200, type: 'dispatch_offer' };
    const payload = {
      role: 'nurse',
      pending_offers: [],
      valid_offer_ids: [200],
      clear_offer_shift_ids: [],
      terminal_shifts: [{ shift_id: 20, status: 'expired' }],
    };
    const result = applyNurseOfferReconcile(payload, {
      currentOffer: current,
      minimizedOffer: null,
      clearShiftOfferUi,
    });
    expect(result.currentOffer).toBeNull();
  });

  it('hydrates pending offer when UI empty and DB has offer', () => {
    const payload = {
      role: 'nurse',
      pending_offers: [
        {
          offer_id: 300,
          shift_id: 30,
          shift_start: new Date(Date.now() + 3600000).toISOString(),
        },
      ],
      valid_offer_ids: [300],
      clear_offer_shift_ids: [],
      terminal_shifts: [],
    };
    const result = applyNurseOfferReconcile(payload, {
      currentOffer: null,
      minimizedOffer: null,
      clearShiftOfferUi,
    });
    expect(result.currentOffer?.offer_id).toBe(300);
    expect(result.changed).toBe(true);
  });

  it('ignores recruiter payloads', () => {
    const current = { shift_id: 1, offer_id: 1 };
    const result = applyNurseOfferReconcile(
      { role: 'recruiter', pending_offers: [] },
      { currentOffer: current, minimizedOffer: null, clearShiftOfferUi },
    );
    expect(result.currentOffer).toEqual(current);
    expect(result.changed).toBe(false);
  });
});

describe('notifyReconcileRefresh', () => {
  it('dispatches nurse refresh events', () => {
    const events = [];
    const handler = (e) => events.push(e.type);
    window.addEventListener('mr-nurse-active-shift-refresh', handler);
    window.addEventListener('mr-jobs-shifts-refresh', handler);
    notifyReconcileRefresh({
      role: 'nurse',
      clear_offer_shift_ids: [5],
      terminal_shifts: [],
    });
    window.removeEventListener('mr-nurse-active-shift-refresh', handler);
    window.removeEventListener('mr-jobs-shifts-refresh', handler);
    expect(events).toContain('mr-nurse-active-shift-refresh');
    expect(events).toContain('mr-jobs-shifts-refresh');
  });
});
