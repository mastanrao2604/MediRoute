/**
 * DispatchOps — Operational Survival Dashboard.
 *
 * Purpose: real-time visibility + manual control for the dispatch system.
 * Access: admin-secret-gated (same pattern as AdminDashboard).
 *
 * Data sources (polling, no WS-over-WS):
 *   - /admin/ops/health-snapshot   every 10s  (in-memory, zero DB)
 *   - /admin/ops/live-shifts        every 15s  (last 2h active shifts)
 *   - /admin/ops/failed-shifts      on-demand  (last 24h failures)
 *   - /admin/ops/timeline/{id}      on-demand  (per-shift incident replay)
 *
 * Actions:
 *   - Toggle dispatch kill switch
 *   - Force-expire stuck session
 *   - Manual assign nurse (links to existing endpoint)
 *   - Re-dispatch failed shift
 *   - View shift timeline
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import api from '../api/axios';

const SESSION_KEY = 'mediroute_admin_secret';

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmt(isoStr) {
  if (!isoStr) return '—';
  const d = new Date(isoStr);
  return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function secToHuman(sec) {
  if (sec == null) return '—';
  if (sec < 60) return `${sec}s`;
  return `${Math.floor(sec / 60)}m ${sec % 60}s`;
}

function Pill({ color, children }) {
  const colors = {
    green:  'bg-green-100 text-green-800',
    red:    'bg-red-100 text-red-800',
    yellow: 'bg-yellow-100 text-yellow-800',
    blue:   'bg-blue-100 text-blue-800',
    gray:   'bg-gray-100 text-gray-600',
  };
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${colors[color] || colors.gray}`}>
      {children}
    </span>
  );
}

function StatCard({ label, value, sub, color }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-4">
      <p className="text-xs text-gray-500 uppercase tracking-wide">{label}</p>
      <p className={`text-2xl font-bold mt-1 ${color || 'text-gray-900'}`}>{value ?? '—'}</p>
      {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
    </div>
  );
}

// ── Timeline drawer ───────────────────────────────────────────────────────────

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
                {data.hospital_name} · {data.role} · {data.urgency} ·{' '}
                <span className="font-medium">{data.status}</span>
              </p>
            )}
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700 text-xl leading-none px-1">&times;</button>
        </div>

        <div className="px-5 py-4">
          {loading && <p className="text-sm text-gray-500">Loading timeline…</p>}
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

// ── Manual Assign Modal ───────────────────────────────────────────────────────

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
        <h3 className="font-bold text-gray-900 mb-1">Manual Assign — Shift #{shiftId}</h3>
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
              {loading ? 'Assigning…' : 'Assign'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function DispatchOps() {
  const [adminSecret, setAdminSecret] = useState(() => sessionStorage.getItem(SESSION_KEY) || '');
  const [secretInput, setSecretInput] = useState('');
  const [authError, setAuthError] = useState('');

  // ── Operational data ──────────────────────────────────────────────────────
  const [health, setHealth] = useState(null);
  const [liveShifts, setLiveShifts] = useState([]);
  const [failedShifts, setFailedShifts] = useState([]);

  // ── UI state ──────────────────────────────────────────────────────────────
  const [activeTab, setActiveTab] = useState('live'); // 'live' | 'failed'
  const [timelineShiftId, setTimelineShiftId] = useState(null);
  const [assignShiftId, setAssignShiftId] = useState(null);
  const [toast, setToast] = useState('');
  const [toggling, setToggling] = useState(false);

  const pollHealth = useRef(null);
  const pollShifts = useRef(null);
  const secretRef = useRef(null);

  // ── API helpers ───────────────────────────────────────────────────────────

  const headers = useCallback(() => ({
    'X-Admin-Secret': adminSecret,
  }), [adminSecret]);

  const showToast = useCallback((msg, duration = 3000) => {
    setToast(msg);
    setTimeout(() => setToast(''), duration);
  }, []);

  const fetchHealth = useCallback(async () => {
    if (!adminSecret) return;
    try {
      const res = await api.get('/admin/ops/health-snapshot', { headers: headers() });
      setHealth(res.data);
    } catch (err) {
      if (err.response?.status === 403) {
        sessionStorage.removeItem(SESSION_KEY);
        setAdminSecret('');
        setAuthError('Admin secret rejected.');
      }
      // Other errors: silently skip (polling will retry)
    }
  }, [adminSecret, headers]);

  const fetchLiveShifts = useCallback(async () => {
    if (!adminSecret) return;
    try {
      const res = await api.get('/admin/ops/live-shifts', { headers: headers() });
      setLiveShifts(res.data.shifts || []);
    } catch { /* silent — polling will retry */ }
  }, [adminSecret, headers]);

  const fetchFailedShifts = useCallback(async () => {
    if (!adminSecret) return;
    try {
      const res = await api.get('/admin/ops/failed-shifts', { headers: headers() });
      setFailedShifts(res.data.failed_shifts || []);
    } catch { /* silent */ }
  }, [adminSecret, headers]);

  // ── Polling setup ─────────────────────────────────────────────────────────

  useEffect(() => {
    if (!adminSecret) return;

    fetchHealth();
    fetchLiveShifts();

    pollHealth.current = setInterval(fetchHealth, 10_000);
    pollShifts.current = setInterval(fetchLiveShifts, 15_000);

    return () => {
      clearInterval(pollHealth.current);
      clearInterval(pollShifts.current);
    };
  }, [adminSecret, fetchHealth, fetchLiveShifts]);

  useEffect(() => {
    if (activeTab === 'failed' && adminSecret) fetchFailedShifts();
  }, [activeTab, adminSecret, fetchFailedShifts]);

  // ── Actions ───────────────────────────────────────────────────────────────

  async function handleToggleDispatch() {
    const newState = !health?.dispatch_enabled;
    if (!window.confirm(`${newState ? 'ENABLE' : 'DISABLE'} dispatch? This takes effect immediately.`)) return;
    setToggling(true);
    try {
      const res = await api.post('/admin/ops/dispatch-toggle',
        { enabled: newState },
        { headers: headers() },
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
    if (!window.confirm(`Force-expire all pending offers in session ${sessionId}?`)) return;
    try {
      const res = await api.post(`/admin/ops/expire-session/${sessionId}`, null, { headers: headers() });
      showToast(res.data.message);
      fetchLiveShifts();
    } catch (err) {
      showToast(err.response?.data?.detail || 'Expire failed.');
    }
  }

  async function handleReDispatch(shiftId) {
    if (!window.confirm(`Re-dispatch shift ${shiftId}?`)) return;
    try {
      await api.post(`/admin/ops/re-dispatch/${shiftId}`, null, { headers: headers() });
      showToast(`Dispatch restarted for shift ${shiftId}.`);
      fetchLiveShifts();
      if (activeTab === 'failed') fetchFailedShifts();
    } catch (err) {
      showToast(err.response?.data?.detail || 'Re-dispatch failed.');
    }
  }

  // ── Secret gate ───────────────────────────────────────────────────────────

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

  // ── Derived values ────────────────────────────────────────────────────────

  const m = health?.dispatch_metrics || {};
  const janitor = health?.janitor || {};

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Toast */}
      {toast && (
        <div className="fixed top-4 left-1/2 -translate-x-1/2 z-50 bg-gray-900 text-white text-sm px-5 py-2.5 rounded-xl shadow-xl">
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

      {/* Header */}
      <div className="bg-white border-b border-gray-200 px-4 py-4 sticky top-0 z-10">
        <div className="max-w-6xl mx-auto flex items-center justify-between gap-3">
          <div>
            <h1 className="text-lg font-bold text-gray-900">Dispatch Ops</h1>
            <p className="text-xs text-gray-500">
              Polling every 10s · {health ? `Last: ${fmt(health.ts)}` : 'Connecting…'}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {/* Kill switch */}
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
                {toggling ? '…' : health.dispatch_enabled ? 'Disable Dispatch' : 'Enable Dispatch'}
              </button>
            )}
            <a
              href="/admin"
              className="text-xs text-indigo-600 hover:underline"
            >
              Recruiters
            </a>
            <button
              onClick={() => { sessionStorage.removeItem(SESSION_KEY); setAdminSecret(''); }}
              className="text-xs text-gray-400 hover:text-gray-600"
            >
              Lock
            </button>
          </div>
        </div>
      </div>

      <div className="max-w-6xl mx-auto px-4 py-6 space-y-6">

        {/* ── Health summary cards ── */}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          <StatCard
            label="Dispatch"
            value={health?.dispatch_enabled ? 'ON' : 'OFF'}
            color={health?.dispatch_enabled ? 'text-green-700' : 'text-red-700'}
          />
          <StatCard
            label="Active Sessions"
            value={health?.active_dispatch_sessions ?? '—'}
            sub="in-progress dispatches"
          />
          <StatCard
            label="WS Connections"
            value={health?.ws_connections ?? '—'}
            sub="live sockets"
          />
          <StatCard
            label="Janitor"
            value={janitor.alive === true ? 'OK' : janitor.alive === false ? 'STALE' : '—'}
            color={janitor.alive === true ? 'text-green-700' : janitor.alive === false ? 'text-red-700' : 'text-gray-500'}
            sub={janitor.last_tick_age_sec != null ? `${janitor.last_tick_age_sec}s ago` : undefined}
          />
          <StatCard
            label="Fill Rate"
            value={m.accept_rate != null ? `${(m.accept_rate * 100).toFixed(0)}%` : '—'}
            sub={m.dispatches_filled != null ? `${m.dispatches_filled} filled` : undefined}
          />
          <StatCard
            label="Avg Fill Time"
            value={m.avg_fill_time_sec != null ? secToHuman(Math.round(m.avg_fill_time_sec)) : '—'}
            sub={m.avg_waves_per_dispatch != null ? `${m.avg_waves_per_dispatch} waves avg` : undefined}
          />
        </div>

        {/* ── Dispatch counter row ── */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <StatCard label="Started" value={m.dispatches_started} />
          <StatCard label="Filled" value={m.dispatches_filled} color="text-green-700" />
          <StatCard label="Expired" value={m.dispatches_expired} color="text-yellow-700" />
          <StatCard label="Failed" value={m.dispatches_failed} color="text-red-700" />
        </div>

        {/* ── Tabs ── */}
        <div className="flex gap-1 border-b border-gray-200">
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

        {/* ── Live shifts table ── */}
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
                      <td className="px-4 py-3 font-medium text-gray-900 max-w-[180px] truncate">{s.hospital_name}</td>
                      <td className="px-4 py-3 text-gray-600 text-xs">{s.role}</td>
                      <td className="px-4 py-3">
                        <Pill color={
                          s.status === 'filled' ? 'green' :
                          s.status === 'dispatching' ? 'blue' :
                          s.status === 'expired' ? 'red' : 'gray'
                        }>
                          {s.status}
                        </Pill>
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-500">
                        {s.dispatch ? `W${s.dispatch.wave ?? '?'} · ${s.dispatch.status}` : '—'}
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-500">{secToHuman(s.fill_time_sec)}</td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <button
                            onClick={() => setTimelineShiftId(s.shift_id)}
                            className="text-xs text-indigo-600 hover:underline"
                          >
                            Timeline
                          </button>
                          {s.dispatch?.status === 'active' && (
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

        {/* ── Failed shifts table ── */}
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
                      <td className="px-4 py-3 font-medium text-gray-900 max-w-[180px] truncate">{s.hospital_name}</td>
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
                        <div className="flex items-center gap-1.5">
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

        {/* ── Janitor + metrics detail ── */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="bg-white rounded-xl border border-gray-200 p-4">
            <h3 className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-3">Janitor</h3>
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between">
                <dt className="text-gray-500">Status</dt>
                <dd><Pill color={janitor.alive ? 'green' : 'red'}>{janitor.alive ? 'Alive' : 'Stale'}</Pill></dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-gray-500">Last tick</dt>
                <dd className="text-gray-700">{janitor.last_tick_age_sec != null ? `${janitor.last_tick_age_sec}s ago` : '—'}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-gray-500">Tick count</dt>
                <dd className="text-gray-700">{janitor.tick_count ?? '—'}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-gray-500">Error count</dt>
                <dd className={`font-medium ${janitor.error_count > 0 ? 'text-red-600' : 'text-gray-700'}`}>
                  {janitor.error_count ?? '—'}
                </dd>
              </div>
            </dl>
          </div>

          <div className="bg-white rounded-xl border border-gray-200 p-4">
            <h3 className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-3">Offer Funnel</h3>
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between">
                <dt className="text-gray-500">Sent</dt>
                <dd className="text-gray-700">{m.offers_sent ?? '—'}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-gray-500">Accepted</dt>
                <dd className="text-green-700 font-medium">{m.offers_accepted ?? '—'}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-gray-500">Declined</dt>
                <dd className="text-yellow-700">{m.offers_declined ?? '—'}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-gray-500">Timed Out</dt>
                <dd className="text-red-600">{m.offers_timed_out ?? '—'}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-gray-500">Accept Rate</dt>
                <dd className="font-medium text-gray-900">
                  {m.accept_rate != null ? `${(m.accept_rate * 100).toFixed(1)}%` : '—'}
                </dd>
              </div>
            </dl>
          </div>
        </div>

      </div>
    </div>
  );
}
