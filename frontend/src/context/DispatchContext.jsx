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
 *   - No server polling — events arrive via the existing WebSocket connection
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
  'shift_filled',
  'shift_expired',
  'dispatch_error',
]);

const DispatchContext = createContext(null);

export function DispatchProvider({ children }) {
  // { shiftId: { ...event, _ts: timestamp, _key: shiftId } }
  const [events, setEvents] = useState({});

  const publish = useCallback((msg) => {
    const shiftId = msg?.shift_id;
    if (!shiftId || !HOSPITAL_EVENT_TYPES.has(msg.type)) return;

    setEvents(prev => {
      const next = { ...prev, [shiftId]: { ...msg, _ts: Date.now() } };

      // Prune if we exceed MAX_EVENTS_STORED (rare — only in high-volume scenarios)
      const keys = Object.keys(next);
      if (keys.length > MAX_EVENTS_STORED) {
        // Remove the oldest entry
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

  return (
    <DispatchContext.Provider value={{ publish, getShiftStatus, getRecentEvents }}>
      {children}
    </DispatchContext.Provider>
  );
}

export function useDispatchEvents() {
  const ctx = useContext(DispatchContext);
  if (!ctx) throw new Error('useDispatchEvents must be used within DispatchProvider');
  return ctx;
}
