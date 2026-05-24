/**
 * DispatchContext — real-time dispatch event store for hospital/recruiter visibility.
 *
 * DispatchManager (in App.jsx) publishes incoming WS events here.
 * RecruiterDashboard reads from this context to show live dispatch activity.
 *
 * Design:
 *   - Events are keyed by shift_id — each shift's latest event overwrites the previous
 *   - Events older than MAX_EVENT_AGE_MS are excluded from getShiftStatus()
 *   - Store is bounded: only the last N events are kept to prevent memory growth
 *   - WS events are a realtime mirror; GET /shifts/ + reconcile clear stale overlay cache
 */
import {
  createContext,
  useContext,
  useState,
  useCallback,
} from 'react';

const MAX_EVENT_AGE_MS = 10 * 60 * 1000; // 10 minutes — dispatch sessions rarely exceed this
const MAX_EVENTS_STORED = 50;             // keep last 50 shifts' events

const HOSPITAL_EVENT_TYPES = new Set([
  'dispatch_started',
  'dispatch_wave_update',
  'nurse_accepted',
  'nurse_applied',
  'shift_search_stopped',
  'shift_filled',
  'shift_expired',
  'shift_cancelled',
  'nurse_no_show',
  'dispatch_error',
]);

const DispatchContext = createContext(null);

export function DispatchProvider({ children }) {
  // { shiftId: { ...event, _ts: timestamp } }
  const [events, setEvents] = useState({});
  // { shiftId: timestamp } — when dispatch_started first arrived for each shift
  const [startTimes, setStartTimes] = useState({});

  const publish = useCallback((msg) => {
    const shiftId = msg?.shift_id;
    if (!shiftId || !HOSPITAL_EVENT_TYPES.has(msg.type)) return;

    const now = Date.now();

    if (msg.type === 'dispatch_started') {
      setStartTimes((prev) => ({ ...prev, [shiftId]: now }));
    }

    // Terminal states — stop elapsed timer
    if (
      msg.type === 'shift_cancelled'
      || msg.type === 'shift_filled'
      || msg.type === 'shift_expired'
      || msg.type === 'shift_search_stopped'
    ) {
      setStartTimes(prev => {
        const next = { ...prev };
        delete next[shiftId];
        return next;
      });
    }

    setEvents(prev => {
      const next = { ...prev, [shiftId]: { ...msg, _ts: now } };

      // Prune if we exceed MAX_EVENTS_STORED (rare — only in high-volume scenarios)
      const keys = Object.keys(next);
      if (keys.length > MAX_EVENTS_STORED) {
        const oldest = keys.reduce((a, b) => (next[a]._ts < next[b]._ts ? a : b));
        delete next[oldest];
      }

      return next;
    });
  }, []);

  /**
   * Get latest dispatch event for a shift.
   * Returns null if no event exists or event has expired from the store.
   */
  const getShiftStatus = useCallback((shiftId) => {
    const ev = events[shiftId];
    if (!ev) return null;
    if (Date.now() - ev._ts > MAX_EVENT_AGE_MS) return null;
    return ev;
  }, [events]);

  /**
   * Returns all recent events, sorted newest-first, up to `limit` items.
   * Used by RecruiterDashboard to show the activity feed.
   */
  const getRecentEvents = useCallback((limit = 10) => {
    const now = Date.now();
    return Object.values(events)
      .filter(ev => now - ev._ts <= MAX_EVENT_AGE_MS)
      .sort((a, b) => b._ts - a._ts)
      .slice(0, limit);
  }, [events]);

  /** Returns the timestamp when dispatch_started was first received for a shift. */
  const getDispatchStartTime = useCallback((shiftId) => {
    return startTimes[shiftId] ?? null;
  }, [startTimes]);

  /** Clear cached events after re-post so expired labels do not stick. */
  const clearShift = useCallback((shiftId) => {
    setEvents((prev) => {
      if (!prev[shiftId]) return prev;
      const next = { ...prev };
      delete next[shiftId];
      return next;
    });
    setStartTimes((prev) => {
      if (!prev[shiftId]) return prev;
      const next = { ...prev };
      delete next[shiftId];
      return next;
    });
  }, []);

  /**
   * Drop WS overlay cache when DB says shift reached a terminal state.
   * Called after GET /shifts/ reconciliation — DB wins over live events.
   */
  const reconcileFromShifts = useCallback((shifts) => {
    if (!Array.isArray(shifts) || shifts.length === 0) return;
    const terminal = new Set(['cancelled', 'expired', 'filled']);
    setEvents((prev) => {
      let next = prev;
      for (const shift of shifts) {
        const sid = shift?.id;
        if (sid == null) continue;
        const dbStatus = shift.status;
        const searchClosed = shift.search_closed || shift.search_active === false;
        const confirmed = (shift.confirmed_count ?? 0) > 0;
        if (
          terminal.has(dbStatus)
          || (searchClosed && confirmed && dbStatus === 'filled')
        ) {
          if (next[sid]) {
            if (next === prev) next = { ...prev };
            delete next[sid];
          }
        }
      }
      return next;
    });
    setStartTimes((prev) => {
      let next = prev;
      for (const shift of shifts) {
        const sid = shift?.id;
        if (sid == null) continue;
        if (terminal.has(shift.status) && next[sid]) {
          if (next === prev) next = { ...prev };
          delete next[sid];
        }
      }
      return next;
    });
  }, []);

  return (
    <DispatchContext.Provider value={{
      publish, getShiftStatus, getRecentEvents, getDispatchStartTime, clearShift,
      reconcileFromShifts,
    }}>
      {children}
    </DispatchContext.Provider>
  );
}

export function useDispatchEvents() {
  const ctx = useContext(DispatchContext);
  if (!ctx) throw new Error('useDispatchEvents must be used within DispatchProvider');
  return ctx;
}
