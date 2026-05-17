/**
 * DispatchOps â€” Internal Operational Dispatch Dashboard.
 *
 * PURPOSE: Real-time operational visibility for pilot monitoring + dispatch debugging.
 * NOT an analytics platform. Optimized for clarity and low latency.
 *
 * Panels (7 requirement areas):
 *   1. Active Dispatch Sessions  â€” in-memory, health-snapshot
 *   2. Nurse Availability        â€” supply-snapshot (30s poll)
 *   3. Dispatch Metrics          â€” engine counters, health-snapshot
 *   4. Offer Visibility          â€” supply-snapshot offer counts
 *   5. WebSocket Health          â€” connection count + stale count
 *   6. System Health             â€” janitor, DB, semaphore, kill switch
 *   7. Failure Visibility        â€” supply-snapshot failure breakdown
 *
 * Polling:
 *   - /admin/ops/health-snapshot  every 10s (in-memory, zero extra DB)
 *   - /admin/ops/supply-snapshot  every 30s (3 efficient GROUP BY queries)
 *   - /admin/ops/live-shifts      every 15s
 *   - /admin/ops/failed-shifts    on-demand (tab switch)
 *
 * Auth: admin-secret-gated + JWT. Same pattern as AdminDashboard.
 * Safety: all intervals cleared on unmount. No WS-over-WS. No memory leaks.
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import api from '../api/axios';

const SESSION_KEY = 'mediroute_admin_secret';

// â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function fmt(isoStr) {
  if (!isoStr) return 'â€”';
  const d = new Date(isoStr);
  return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function secToHuman(sec) {
  if (sec == null) return 'â€”';
  if (sec < 60) return `${sec}s`;
  return `${Math.floor(sec / 60)}m ${sec % 60}s`;
}

function pct(n, d) {
  if (!d || d === 0) return 'â€”';
  return `${((n / d) * 100).toFixed(0)}%`;
}

// â”€â”€ Primitive components â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function Pill({ color, children }) {
  const colors = {
    green:  'bg-green-100 text-green-800',
    red:    'bg-red-100 text-red-800',
    yellow: 'bg-yellow-100 text-yellow-800',
    blue:   'bg-blue-100 text-blue-800',
    gray:   'bg-gray-100 text-gray-600',
    orange: 'bg-orange-100 text-orange-700',
  };
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${colors[color] || colors.gray}`}>
      {children}
    </span>
  );
}

function StatCard({ label, value, sub, color, alert }) {
  return (
    <div className={`bg-white rounded-xl border p-4 ${alert ? 'border-red-300 bg-red-50' : 'border-gray-200'}`}>
      <p className="text-xs text-gray-500 uppercase tracking-wide">{label}</p>
      <p className={`text-2xl font-bold mt-1 ${color || 'text-gray-900'}`}>{value ?? 'â€”'}</p>
      {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
    </div>
  );
}

function SectionTitle({ children }) {
  return (
    <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
      {children}
    </h2>
  );
}

function KVRow({ label, value, valueClass }) {
  return (
    <div className="flex justify-between items-center py-1.5 border-b border-gray-50 last:border-0">
      <dt className="text-sm text-gray-500">{label}</dt>
      <dd className={`text-sm font-medium ${valueClass || 'text-gray-800'}`}>{value ?? 'â€”'}</dd>
    </div>
  );
}

// â”€â”€ Timeline drawer â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const EVENT_LABELS = {
  'shift.created':          { label: 'Shift Created',     color: 'blue'   },
  'shift.dispatching':      { label: 'Dispatching',        color: 'blue'   },
  'shift.filled':           { label: 'Shift Filled',       color: 'green'  },
  'shift.expired':          { label: 'Shift Expired',      color: 'red'    },
  'shift.cancelled':        { label: 'Cancelled',          color: 'red'    },
  'offer.sent':             { label: 'Offers Sent',        color: 'blue'   },
  'offer.accepted':         { label: 'Offer Accepted',     color: 'green'  },
  'offer.declined':         { label: 'Offer Declined',     color: 'yellow' },
  'offer.timed_out':        { label: 'Offer Expired',      color: 'yellow' },
  'offer.cancelled':        { label: 'Offer Cancelled',    color: 'gray'   },
  'dispatch.wave_exhausted':{ label: 'Wave Exhausted',     color: 'yellow' },
  'dispatch.failed':        { label: 'Dispatch Failed',    color: 'red'    },
  'dispatch.manual_override':{ label: 'Manual Override',  color: 'blue'   },
};

function TimelineDrawer({ shiftId, onClose, adminSecret }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError('');
      try {
        const res = await api.get(`/admin/ops/timeline/${shiftId}`, {
          headers: { 'X-Admin-Secret': adminSecret },
        });
        if (!cancelled) setData(res.data);
      } catch {
        if (!cancelled) setError('Failed to load timeline.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [shiftId, adminSecret]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-end"
      onClick={onClose}
    >
      <div
        className="relative bg-white w-full max-w-lg h-full shadow-2xl overflow-y-auto"
        onClick={e => e.stopPropagation()}
      >
        <div className="sticky top-0 bg-white border-b border-gray-200 px-5 py-4 flex items-center justify-between">
          <div>
            <h3 className="font-bold text-gray-900 text-sm">Shift #{shiftId} Timeline</h3>
            {data && (
              <p className="text-xs text-gray-500 mt-0.5">
                {data.hospital_name} Â· {data.role} Â· {data.urgency} Â·{' '}
                <span className="font-medium">{data.status}</span>
              </p>
            )}
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700 text-xl leading-none px-1">&times;</button>
        </div>

        <div className="px-5 py-4">
          {loading && <p className="text-sm text-gray-500">Loading timelineâ€¦</p>}
          {error && <p className="text-sm text-red-600">{error}</p>}
          {data && data.timeline.length === 0 && (
            <p className="text-sm text-gray-400">No timeline events yet.</p>
          )}
          {data && data.timeline.length > 0 && (
            <ol className="relative border-l border-gray-200 ml-2">
              {data.timeline.map((ev, i) => {
                const meta = EVENT_LABELS[ev.event_type] || { label: ev.event_type, color: 'gray' };
                return (
                  <li key={ev.id} className="mb-5 ml-4">
                    <div className="absolute -left-1.5 w-3 h-3 rounded-full bg-gray-300 border-2 border-white" />
                    <div className="flex items-center gap-2 mb-1">
                      <Pill color={meta.color}>{meta.label}</Pill>
                      <span className="text-xs text-gray-400">{fmt(ev.occurred_at)}</span>
                    </div>
                    {ev.payload && Object.keys(ev.payload).length > 0 && (
                      <pre className="text-xs text-gray-600 bg-gray-50 rounded p-2 overflow-x-auto whitespace-pre-wrap">
                        {JSON.stringify(ev.payload, null, 2)}
                      </pre>
                    )}
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      </div>
    </div>
  );
}

// â”€â”€ Manual Assign Modal â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function ManualAssignModal({ shiftId, onClose, adminSecret, onSuccess }) {
  const [nurseId, setNurseId] = useState('');
  const [reason, setReason] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function handleSubmit(e) {
    e.preventDefault();
    if (!nurseId || !reason) return;
    setLoading(true);
    setError('');
    try {
      await api.post('/admin/ops/manual-assign', {
        shift_id: parseInt(shiftId, 10),
        nurse_user_id: parseInt(nurseId, 10),
        reason,
      }, { headers: { 'X-Admin-Secret': adminSecret } });
      onSuccess('Manual assignment complete.');
      onClose();
    } catch (err) {
      setError(err.response?.data?.detail || 'Assignment failed.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-6" onClick={e => e.stopPropagation()}>
        <h3 className="font-bold text-gray-900 mb-1">Manual Assign â€” Shift #{shiftId}</h3>
        <p className="text-xs text-gray-500 mb-4">Bypasses dispatch engine. Use for urgent recovery only.</p>
        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <input
            type="number"
            placeholder="Nurse User ID"
            value={nurseId}
            onChange={e => setNurseId(e.target.value)}
            className="border border-gray-300 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            required
          />
          <input
            type="text"
            placeholder="Reason (for audit log)"
            value={reason}
            onChange={e => setReason(e.target.value)}
            className="border border-gray-300 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            required
          />
          {error && <p className="text-xs text-red-600">{error}</p>}
          <div className="flex gap-2 mt-1">
            <button type="button" onClick={onClose} className="flex-1 border border-gray-300 text-gray-700 text-sm font-medium py-2.5 rounded-xl hover:bg-gray-50">
              Cancel
            </button>
            <button type="submit" disabled={loading} className="flex-1 bg-indigo-600 text-white text-sm font-semibold py-2.5 rounded-xl hover:bg-indigo-700 disabled:opacity-60">
              {loading ? 'Assigningâ€¦' : 'Assign'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// â”€â”€ Main Component â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export default function DispatchOps() {
  const [adminSecret, setAdminSecret] = useState(() => sessionStorage.getItem(SESSION_KEY) || '');
  const [secretInput, setSecretInput] = useState('');
  const [authError, setAuthError] = useState('');

  // â”€â”€ Operational data â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const [health, setHealth] = useState(null);           // health-snapshot (10s)
  const [supply, setSupply] = useState(null);           // supply-snapshot (30s)
  const [liveShifts, setLiveShifts] = useState([]);     // live-shifts (15s)
  const [failedShifts, setFailedShifts] = useState([]); // on-demand

  // â”€â”€ UI state â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const [activeTab, setActiveTab] = useState('live'); // 'live' | 'failed'
  const [timelineShiftId, setTimelineShiftId] = useState(null);
  const [assignShiftId, setAssignShiftId] = useState(null);
  const [toast, setToast] = useState('');
  const [toggling, setToggling] = useState(false);

  // Interval refs â€” cleared on unmount to prevent memory leaks
  const pollHealth = useRef(null);
  const pollSupply = useRef(null);
  const pollShifts = useRef(null);

  // â”€â”€ API helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  const hdrs = useCallback(() => ({
    'X-Admin-Secret': adminSecret,
  }), [adminSecret]);

  const showToast = useCallback((msg, duration = 3500) => {
    setToast(msg);
    setTimeout(() => setToast(''), duration);
  }, []);

  const fetchHealth = useCallback(async () => {
    if (!adminSecret) return;
    try {
      const res = await api.get('/admin/ops/health-snapshot', { headers: hdrs() });
      setHealth(res.data);
    } catch (err) {
      if (err.response?.status === 403) {
        sessionStorage.removeItem(SESSION_KEY);
        setAdminSecret('');
        setAuthError('Admin secret rejected.');
      }
      // Other errors: silently skip â€” polling will retry
    }
  }, [adminSecret, hdrs]);

  const fetchSupply = useCallback(async () => {
    if (!adminSecret) return;
    try {
      const res = await api.get('/admin/ops/supply-snapshot', { headers: hdrs() });
      setSupply(res.data);
    } catch { /* silent â€” non-critical */ }
  }, [adminSecret, hdrs]);

  const fetchLiveShifts = useCallback(async () => {
    if (!adminSecret) return;
    try {
      const res = await api.get('/admin/ops/live-shifts', { headers: hdrs() });
      setLiveShifts(res.data.shifts || []);
    } catch { /* silent */ }
  }, [adminSecret, hdrs]);

  const fetchFailedShifts = useCallback(async () => {
    if (!adminSecret) return;
    try {
      const res = await api.get('/admin/ops/failed-shifts', { headers: hdrs() });
      setFailedShifts(res.data.failed_shifts || []);
    } catch { /* silent */ }
  }, [adminSecret, hdrs]);

  // â”€â”€ Polling setup â€” all intervals cleared on unmount â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  useEffect(() => {
    if (!adminSecret) return;

    // Initial fetches
    fetchHealth();
    fetchSupply();
    fetchLiveShifts();

    pollHealth.current = setInterval(fetchHealth, 10_000);    // 10s in-memory
    pollSupply.current = setInterval(fetchSupply, 30_000);    // 30s 3 DB queries
    pollShifts.current = setInterval(fetchLiveShifts, 15_000); // 15s

    return () => {
      clearInterval(pollHealth.current);
      clearInterval(pollSupply.current);
      clearInterval(pollShifts.current);
    };
  }, [adminSecret, fetchHealth, fetchSupply, fetchLiveShifts]);

  useEffect(() => {
    if (activeTab === 'failed' && adminSecret) fetchFailedShifts();
  }, [activeTab, adminSecret, fetchFailedShifts]);

  // â”€â”€ Actions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  async function handleToggleDispatch() {
    const newState = !health?.dispatch_enabled;
    if (!window.confirm(`${newState ? 'ENABLE' : 'DISABLE'} dispatch? This takes effect immediately.`)) return;
    setToggling(true);
    try {
      const res = await api.post('/admin/ops/dispatch-toggle',
        { enabled: newState },
        { headers: hdrs() },
      );
      setHealth(prev => prev ? { ...prev, dispatch_enabled: res.data.dispatch_enabled } : prev);
      showToast(res.data.message);
    } catch (err) {
      showToast(err.response?.data?.detail || 'Toggle failed.');
    } finally {
      setToggling(false);
    }
  }

  async function handleExpireSession(sessionId) {
    if (!sessionId) return;
    if (!window.confirm(`Force-expire all pending offers in session ${sessionId}?`)) return;
    try {
      const res = await api.post(`/admin/ops/expire-session/${sessionId}`, null, { headers: hdrs() });
      showToast(res.data.message);
      fetchLiveShifts();
    } catch (err) {
      showToast(err.response?.data?.detail || 'Expire failed.');
    }
  }

  async function handleReDispatch(shiftId) {
    if (!window.confirm(`Re-dispatch shift ${shiftId}?`)) return;
    try {
      await api.post(`/admin/ops/re-dispatch/${shiftId}`, null, { headers: hdrs() });
      showToast(`Dispatch restarted for shift ${shiftId}.`);
      fetchLiveShifts();
      if (activeTab === 'failed') fetchFailedShifts();
    } catch (err) {
      showToast(err.response?.data?.detail || 'Re-dispatch failed.');
    }
  }

  // â”€â”€ Secret gate â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  function handleSecretSubmit(e) {
    e.preventDefault();
    const val = secretInput.trim();
    if (!val) return;
    sessionStorage.setItem(SESSION_KEY, val);
    setAdminSecret(val);
    setSecretInput('');
    setAuthError('');
  }

  if (!adminSecret) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
        <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-8 w-full max-w-sm">
          <h2 className="text-lg font-bold text-gray-900 mb-1">Ops Access</h2>
          <p className="text-sm text-gray-500 mb-5">Enter admin secret to access Dispatch Ops.</p>
          <form onSubmit={handleSecretSubmit} className="flex flex-col gap-3">
            <input
              ref={secretRef}
              type="password"
              value={secretInput}
              onChange={e => setSecretInput(e.target.value)}
              placeholder="Admin secret"
              autoFocus
              className="border border-gray-300 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
            {authError && <p className="text-red-600 text-xs">{authError}</p>}
            <button type="submit" className="bg-indigo-600 text-white font-semibold py-3 rounded-xl text-sm hover:bg-indigo-700">
              Unlock
            </button>
          </form>
        </div>
      </div>
    );
  }

  // â”€â”€ Derived values â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const m = health?.dispatch_metrics || {};
  const janitor = health?.janitor || {};
  const semaphore = health?.semaphore || {};
  const nurses = supply?.nurses || {};
  const offers = supply?.offers || {};
  const failures = supply?.failures || {};

  const totalOffers = (offers.accepted || 0) + (offers.declined || 0) + (offers.timed_out || 0);
  const totalFailureEvents =
    (failures.wave_exhausted || 0) + (failures.dispatch_failed || 0) +
    (failures.shift_expired || 0) + (failures.shift_cancelled || 0);
  const semaphoreHot = semaphore.capacity > 0 && (semaphore.in_use / semaphore.capacity) >= 0.8;
  const janitorStale = janitor.alive === false;
  const wsHigh = (health?.ws_stale || 0) > 0;

  return (
    <div className="min-h-screen bg-gray-50">

      {/* Toast notification */}
      {toast && (
        <div className="fixed top-4 left-1/2 -translate-x-1/2 z-50 bg-gray-900 text-white text-sm px-5 py-2.5 rounded-xl shadow-xl pointer-events-none">
          {toast}
        </div>
      )}

      {/* Timeline drawer */}
      {timelineShiftId && (
        <TimelineDrawer
          shiftId={timelineShiftId}
          onClose={() => setTimelineShiftId(null)}
          adminSecret={adminSecret}
        />
      )}

      {/* Manual assign modal */}
      {assignShiftId && (
        <ManualAssignModal
          shiftId={assignShiftId}
          onClose={() => setAssignShiftId(null)}
          adminSecret={adminSecret}
          onSuccess={showToast}
        />
      )}

      {/* â”€â”€ Header â”€â”€ */}
      <div className="bg-white border-b border-gray-200 px-4 py-4 sticky top-0 z-10">
        <div className="max-w-7xl mx-auto flex items-center justify-between gap-3">
          <div>
            <h1 className="text-lg font-bold text-gray-900">Dispatch Ops</h1>
            <p className="text-xs text-gray-500">
              {health ? `Updated ${fmt(health.ts)}` : 'Connectingâ€¦'}
              {supply ? ` Â· Supply ${fmt(supply.ts)}` : ''}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {health != null && (
              <button
                onClick={handleToggleDispatch}
                disabled={toggling}
                className={`text-xs font-semibold px-4 py-2 rounded-lg transition-colors ${
                  health.dispatch_enabled
                    ? 'bg-red-100 text-red-700 hover:bg-red-200'
                    : 'bg-green-100 text-green-700 hover:bg-green-200'
                }`}
              >
                {toggling ? 'â€¦' : health.dispatch_enabled ? 'ðŸ”´ Disable Dispatch' : 'ðŸŸ¢ Enable Dispatch'}
              </button>
            )}
            <a href="/admin" className="text-xs text-indigo-600 hover:underline hidden sm:inline">Admin</a>
            <button
              onClick={() => { sessionStorage.removeItem(SESSION_KEY); setAdminSecret(''); }}
              className="text-xs text-gray-400 hover:text-gray-600"
            >
              Lock
            </button>
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-4 py-5 space-y-6">

        {/* â•â• Â§ 6 Â· SYSTEM HEALTH â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */}
        <section>
          <SectionTitle>System Health</SectionTitle>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
            <StatCard
              label="Dispatch"
              value={health?.dispatch_enabled === true ? 'ON' : health?.dispatch_enabled === false ? 'OFF' : 'â€”'}
              color={health?.dispatch_enabled ? 'text-green-700' : 'text-red-700'}
              alert={health?.dispatch_enabled === false}
            />
            <StatCard
              label="Janitor"
              value={janitor.alive === true ? 'OK' : janitor.alive === false ? 'STALE' : 'â€”'}
              color={janitor.alive === true ? 'text-green-700' : janitor.alive === false ? 'text-red-700' : 'text-gray-500'}
              sub={janitor.last_tick_age_sec != null ? `${janitor.last_tick_age_sec}s ago` : undefined}
              alert={janitorStale}
            />
            <StatCard
              label="DB"
              value={supply?.db_ok === true ? 'OK' : supply == null ? 'â€”' : 'ERR'}
              color={supply?.db_ok ? 'text-green-700' : 'text-red-700'}
              sub={supply ? `${supply.window_hours}h window` : undefined}
              alert={supply != null && !supply.db_ok}
            />
            <StatCard
              label="Active Sessions"
              value={health?.active_dispatch_sessions ?? 'â€”'}
              sub="in-progress"
            />
            <StatCard
              label="Semaphore"
              value={semaphore.capacity ? `${semaphore.in_use}/${semaphore.capacity}` : 'â€”'}
              sub={semaphore.available != null ? `${semaphore.available} free` : undefined}
              color={semaphoreHot ? 'text-orange-700' : 'text-gray-900'}
              alert={semaphoreHot}
            />
            <StatCard
              label="Janitor Errors"
              value={janitor.error_count ?? 'â€”'}
              sub={janitor.tick_count != null ? `${janitor.tick_count} ticks` : undefined}
              color={(janitor.error_count || 0) > 0 ? 'text-red-600' : 'text-gray-900'}
              alert={(janitor.error_count || 0) > 0}
            />
          </div>
        </section>

        {/* â•â• Â§ 5 Â· WEBSOCKET HEALTH â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */}
        <section>
          <SectionTitle>WebSocket Health</SectionTitle>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <StatCard
              label="Active Connections"
              value={health?.ws_connections ?? 'â€”'}
              sub="live sockets"
            />
            <StatCard
              label="Stale Sockets"
              value={health?.ws_stale ?? 'â€”'}
              sub="silent >90s"
              color={wsHigh ? 'text-orange-700' : 'text-gray-900'}
              alert={wsHigh}
            />
            <StatCard
              label="Janitor Interval"
              value={janitor.interval_sec ? `${janitor.interval_sec}s` : 'â€”'}
              sub="prune cycle"
            />
            <StatCard
              label="Last Prune"
              value={janitor.last_tick_age_sec != null ? `${janitor.last_tick_age_sec}s` : 'â€”'}
              sub="ago"
              color={(janitor.last_tick_age_sec || 0) > 90 ? 'text-orange-700' : 'text-gray-900'}
            />
          </div>
        </section>

        {/* â•â• Â§ 2 Â· NURSE AVAILABILITY â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */}
        <section>
          <SectionTitle>Nurse Availability â€” {supply?.city_id || 'â€¦'} (fresh â‰¤5 min)</SectionTitle>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <StatCard
              label="Available"
              value={nurses.online_available ?? 'â€”'}
              sub="ready for dispatch"
              color="text-green-700"
            />
            <StatCard
              label="Busy"
              value={nurses.online_busy ?? 'â€”'}
              sub="on assignment"
              color="text-orange-700"
            />
            <StatCard
              label="Background"
              value={nurses.background ?? 'â€”'}
              sub="app open, inactive"
            />
            <StatCard
              label="Total Fresh"
              value={nurses.total_fresh ?? 'â€”'}
              sub={`supply pool (${nurses.freshness_window_min || 5}m window)`}
            />
          </div>
        </section>

        {/* â•â• Â§ 1 & 3 Â· DISPATCH SESSIONS + METRICS â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */}
        <section>
          <SectionTitle>Dispatch Sessions & Metrics (process lifetime)</SectionTitle>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <StatCard label="Started"  value={m.dispatches_started} />
            <StatCard label="Filled"   value={m.dispatches_filled}  color="text-green-700" />
            <StatCard label="Expired"  value={m.dispatches_expired} color="text-yellow-700" />
            <StatCard label="Failed"   value={m.dispatches_failed}  color="text-red-700"
              alert={(m.dispatches_failed || 0) > 0} />
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-3">
            <StatCard
              label="Avg Fill Time"
              value={m.avg_fill_time_sec != null ? secToHuman(Math.round(m.avg_fill_time_sec)) : 'â€”'}
              sub={m.avg_waves_per_dispatch != null ? `${m.avg_waves_per_dispatch} waves avg` : undefined}
            />
            <StatCard
              label="Accept Rate"
              value={m.accept_rate != null ? `${(m.accept_rate * 100).toFixed(1)}%` : 'â€”'}
              color={m.accept_rate != null && m.accept_rate < 0.3 ? 'text-red-600' : 'text-gray-900'}
            />
            <StatCard
              label="Timeout Rate"
              value={m.timeout_rate != null ? `${(m.timeout_rate * 100).toFixed(1)}%` : 'â€”'}
              sub="of responded offers"
              color={m.timeout_rate != null && m.timeout_rate > 0.5 ? 'text-orange-700' : 'text-gray-900'}
            />
            <StatCard
              label="Offers Sent"
              value={m.offers_sent ?? 'â€”'}
              sub="total this session"
            />
          </div>
        </section>

        {/* â•â• Â§ 4 Â· OFFER VISIBILITY + ENGINE COUNTERS â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */}
        <section>
          <SectionTitle>Offer Pipeline â€” last {supply?.window_hours || 4}h</SectionTitle>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="bg-white rounded-xl border border-gray-200 p-4">
              <dl className="space-y-0">
                <KVRow label="Pending (active)" value={offers.pending ?? 'â€”'} valueClass="text-blue-700 font-semibold" />
                <KVRow label="Accepted" value={offers.accepted ?? 'â€”'} valueClass="text-green-700 font-semibold" />
                <KVRow label="Declined" value={offers.declined ?? 'â€”'} valueClass="text-yellow-700" />
                <KVRow label="Timed Out" value={offers.timed_out ?? 'â€”'} valueClass="text-red-600" />
                <KVRow label="Cancelled" value={offers.cancelled ?? 'â€”'} />
                <KVRow
                  label="Accept Rate"
                  value={pct(offers.accepted || 0, totalOffers)}
                  valueClass={totalOffers > 0 && (offers.accepted / totalOffers) < 0.3 ? 'text-red-600 font-semibold' : 'font-semibold'}
                />
              </dl>
            </div>
            <div className="bg-white rounded-xl border border-gray-200 p-4">
              <h3 className="text-xs font-medium text-gray-400 uppercase tracking-wider mb-3">Engine Counters (in-memory)</h3>
              <dl className="space-y-0">
                <KVRow label="Offers Sent" value={m.offers_sent ?? 'â€”'} />
                <KVRow label="Accepted" value={m.offers_accepted ?? 'â€”'} valueClass="text-green-700" />
                <KVRow label="Declined" value={m.offers_declined ?? 'â€”'} valueClass="text-yellow-700" />
                <KVRow label="Timed Out" value={m.offers_timed_out ?? 'â€”'} valueClass="text-red-600" />
              </dl>
            </div>
          </div>
        </section>

        {/* â•â• Â§ 7 Â· FAILURE VISIBILITY â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */}
        <section>
          <SectionTitle>Failure Breakdown â€” last {supply?.window_hours || 4}h</SectionTitle>
          <div className="bg-white rounded-xl border border-gray-200 p-4">
            {totalFailureEvents === 0 && supply != null ? (
              <p className="text-sm text-gray-400 text-center py-2">No failure events in this window.</p>
            ) : (
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-center">
                <div>
                  <p className={`text-2xl font-bold ${(failures.wave_exhausted || 0) > 0 ? 'text-orange-600' : 'text-gray-400'}`}>
                    {failures.wave_exhausted ?? 'â€”'}
                  </p>
                  <p className="text-xs text-gray-500 mt-1 font-medium">Wave Exhausted</p>
                  <p className="text-xs text-gray-400">No nurses in radius</p>
                </div>
                <div>
                  <p className={`text-2xl font-bold ${(failures.dispatch_failed || 0) > 0 ? 'text-red-600' : 'text-gray-400'}`}>
                    {failures.dispatch_failed ?? 'â€”'}
                  </p>
                  <p className="text-xs text-gray-500 mt-1 font-medium">Engine Error</p>
                  <p className="text-xs text-gray-400">dispatch.failed event</p>
                </div>
                <div>
                  <p className={`text-2xl font-bold ${(failures.shift_expired || 0) > 0 ? 'text-yellow-600' : 'text-gray-400'}`}>
                    {failures.shift_expired ?? 'â€”'}
                  </p>
                  <p className="text-xs text-gray-500 mt-1 font-medium">Shifts Expired</p>
                  <p className="text-xs text-gray-400">All waves used up</p>
                </div>
                <div>
                  <p className={`text-2xl font-bold text-gray-400`}>
                    {failures.shift_cancelled ?? 'â€”'}
                  </p>
                  <p className="text-xs text-gray-500 mt-1 font-medium">Cancelled</p>
                  <p className="text-xs text-gray-400">Manual cancel</p>
                </div>
              </div>
            )}
          </div>
        </section>

        {/* â•â• SHIFT TABLES (Live + Failed) â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */}
        <section>
          <div className="flex gap-1 border-b border-gray-200 mb-4">
            {['live', 'failed'].map(tab => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-4 py-2 text-sm font-medium capitalize transition-colors ${
                  activeTab === tab
                    ? 'border-b-2 border-indigo-600 text-indigo-600'
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                {tab === 'live' ? `Live Shifts (${liveShifts.length})` : `Failed (${failedShifts.length})`}
              </button>
            ))}
          </div>

          {/* Live shifts table */}
          {activeTab === 'live' && (
            <div className="bg-white rounded-xl border border-gray-200 overflow-x-auto">
              {liveShifts.length === 0 ? (
                <p className="text-sm text-gray-400 text-center py-10">No active shifts in the last 2 hours.</p>
              ) : (
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">Shift</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">Hospital</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">Role</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">Status</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">Wave</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">Fill Time</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50">
                    {liveShifts.map(s => (
                      <tr key={s.shift_id} className="hover:bg-gray-50">
                        <td className="px-4 py-3 font-mono text-xs text-gray-500">#{s.shift_id}</td>
                        <td className="px-4 py-3 font-medium text-gray-900 max-w-[160px] truncate">{s.hospital_name}</td>
                        <td className="px-4 py-3 text-gray-600 text-xs">{s.role}</td>
                        <td className="px-4 py-3">
                          <Pill color={
                            s.status === 'filled'      ? 'green' :
                            s.status === 'dispatching' ? 'blue' :
                            s.status === 'expired'     ? 'red' : 'gray'
                          }>
                            {s.status}
                          </Pill>
                        </td>
                        <td className="px-4 py-3 text-xs text-gray-500">
                          {s.dispatch ? `W${s.dispatch.wave ?? '?'} Â· ${s.dispatch.status}` : 'â€”'}
                        </td>
                        <td className="px-4 py-3 text-xs text-gray-500">{secToHuman(s.fill_time_sec)}</td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2 flex-wrap">
                            <button
                              onClick={() => setTimelineShiftId(s.shift_id)}
                              className="text-xs text-indigo-600 hover:underline"
                            >
                              Timeline
                            </button>
                            {s.dispatch?.status === 'active' && s.dispatch?.session_id && (
                              <button
                                onClick={() => handleExpireSession(s.dispatch.session_id)}
                                className="text-xs text-yellow-600 hover:underline"
                              >
                                Expire
                              </button>
                            )}
                            {s.status !== 'filled' && (
                              <button
                                onClick={() => setAssignShiftId(s.shift_id)}
                                className="text-xs text-green-700 hover:underline"
                              >
                                Assign
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}

          {/* Failed shifts table */}
          {activeTab === 'failed' && (
            <div className="bg-white rounded-xl border border-gray-200 overflow-x-auto">
              {failedShifts.length === 0 ? (
                <p className="text-sm text-gray-400 text-center py-10">No failed shifts in the last 24 hours.</p>
              ) : (
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">Shift</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">Hospital</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">Role</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">Urgency</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">Status</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">Created</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50">
                    {failedShifts.map(s => (
                      <tr key={s.shift_id} className="hover:bg-gray-50">
                        <td className="px-4 py-3 font-mono text-xs text-gray-500">#{s.shift_id}</td>
                        <td className="px-4 py-3 font-medium text-gray-900 max-w-[160px] truncate">{s.hospital_name}</td>
                        <td className="px-4 py-3 text-gray-600 text-xs">{s.role}</td>
                        <td className="px-4 py-3">
                          <Pill color={s.urgency === 'emergency' ? 'red' : s.urgency === 'urgent' ? 'yellow' : 'gray'}>
                            {s.urgency}
                          </Pill>
                        </td>
                        <td className="px-4 py-3">
                          <Pill color="red">{s.status}</Pill>
                        </td>
                        <td className="px-4 py-3 text-xs text-gray-500">{fmt(s.created_at)}</td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            <button
                              onClick={() => setTimelineShiftId(s.shift_id)}
                              className="text-xs text-indigo-600 hover:underline"
                            >
                              Timeline
                            </button>
                            <button
                              onClick={() => handleReDispatch(s.shift_id)}
                              className="text-xs text-green-700 hover:underline"
                            >
                              Re-Dispatch
                            </button>
                            <button
                              onClick={() => setAssignShiftId(s.shift_id)}
                              className="text-xs text-gray-600 hover:underline"
                            >
                              Assign
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </section>

      </div>
    </div>
  );
}
