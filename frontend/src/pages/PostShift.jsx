import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api/axios';
import MainLayout from '../layouts/MainLayout';
import { useAuth } from '../context/AuthContext';
import { mlog, mlogError } from '../utils/mobileLogger';
import { captureCurrentArea, openAppSettings } from '../utils/deviceLocation';
import { geocodePincode } from '../utils/geocodePincode';
import { datetimeLocalToUtcIso, nowDatetimeLocalPlusMinutes } from '../utils/shiftDateTime';

const ROLES = [
  { value: 'nurse',           label: 'Nurse' },
  { value: 'staff_nurse',     label: 'Staff Nurse' },
  { value: 'icu_nurse',       label: 'ICU Nurse' },
  { value: 'ot_nurse',        label: 'OT Nurse' },
  { value: 'emergency_nurse', label: 'Emergency Nurse' },
  { value: 'home_care_nurse', label: 'Home Care Nurse' },
  { value: 'doctor',          label: 'Doctor' },
  { value: 'lab_tech',        label: 'Lab Technician' },
  { value: 'pharmacist',      label: 'Pharmacist' },
  { value: 'driver',          label: 'Driver' },
  { value: 'front_office',    label: 'Front Office' },
];

// Human-friendly labels — backend urgency enum values stay unchanged
const URGENCY = [
  {
    value: 'emergency',
    icon: '🔴',
    title: 'Right Now',
    desc: 'Need someone immediately — critical gap',
    border: 'border-red-300',
    bg: 'bg-red-50',
    title_color: 'text-red-700',
  },
  {
    value: 'urgent',
    icon: '🟠',
    title: 'Within a Few Hours',
    desc: 'Needed today, have a little time',
    border: 'border-orange-300',
    bg: 'bg-orange-50',
    title_color: 'text-orange-700',
  },
  {
    value: 'standard',
    icon: '🔵',
    title: "Today's Shift",
    desc: 'Regular same-day scheduling',
    border: 'border-blue-300',
    bg: 'bg-blue-50',
    title_color: 'text-blue-700',
  },
  {
    value: 'planned',
    icon: '⚪',
    title: 'Plan Ahead',
    desc: 'Future requirement — next day or later',
    border: 'border-gray-300',
    bg: 'bg-gray-50',
    title_color: 'text-gray-700',
  },
];

export default function PostShift() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const pincodeRef = useRef(null);

  const [form, setForm] = useState({
    role_required: 'nurse',
    specialty: '',
    hospital_name: user?.company_name || '',
    shift_start: nowDatetimeLocalPlusMinutes(30),
    shift_end: '',
    urgency: 'standard',
    pay_rate: '',
    notes: '',
    dispatch_radius_km: '10',
  });

  // Location — lat/lng are internal only, never shown to recruiter
  const [lat, setLat] = useState(null);
  const [lng, setLng] = useState(null);
  // 'detecting' | 'gps_ok' | 'pincode_mode' | 'geocoding' | 'geocode_ok' | 'geocode_err'
  const [locMode, setLocMode] = useState('detecting');
  const [pincode, setPincode] = useState('');
  const [areaLabel, setAreaLabel] = useState(''); // human-readable confirmed area
  const [geocodeErr, setGeocodeErr] = useState('');
  /** Resolved from GPS via reverse geocode (6-digit pin when available). */
  const [gpsDerivedPincode, setGpsDerivedPincode] = useState('');
  const [gpsRevWarn, setGpsRevWarn] = useState('');
  const [locErr, setLocErr] = useState('');
  const [showLocSettings, setShowLocSettings] = useState(false);

  const [formError, setFormError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    tryGPS();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once on mount
  }, []);

  async function tryGPS() {
    setGpsDerivedPincode('');
    setGpsRevWarn('');
    setAreaLabel('');
    setLocErr('');
    setShowLocSettings(false);
    setLocMode('detecting');
    const cap = await captureCurrentArea({ audience: 'recruiter', highAccuracy: true });
    if (cap.ok) {
      setLat(cap.lat);
      setLng(cap.lng);
      if (cap.pincode) setGpsDerivedPincode(cap.pincode);
      if (cap.locality && cap.pincode) {
        setAreaLabel(`${cap.locality} — ${cap.pincode}`);
      } else if (cap.locality) {
        setAreaLabel(cap.locality);
      }
      setLocMode('gps_ok');
      mlog('location', 'post_shift_gps_ok', { locality: cap.locality?.slice(0, 24) });
      return;
    }
    if (cap.lat != null && cap.lng != null) {
      setLat(cap.lat);
      setLng(cap.lng);
      setLocMode('gps_ok');
      setGpsRevWarn(cap.userMessage || 'Enter pincode manually for best matching.');
      return;
    }
    setLocMode('pincode_mode');
    setLocErr(cap.userMessage || 'Could not detect location.');
    setShowLocSettings(cap.permissionState === 'permanent');
    mlog('location', 'post_shift_gps_fail', { state: cap.permissionState });
    setTimeout(() => pincodeRef.current?.focus(), 100);
  }

  async function handlePincodeConfirm() {
    const clean = pincode.replace(/\D/g, '');
    if (clean.length !== 6) {
      setGeocodeErr('Enter a valid 6-digit pincode.');
      return;
    }
    setGeocodeErr('');
    setLocMode('geocoding');
    try {
      const result = await geocodePincode(clean);
      setLat(result.lat);
      setLng(result.lng);
      setAreaLabel(`${result.displayName} — ${clean}`);
      setLocMode('geocode_ok');
      console.log('[PostShift] geocode ok:', result.lat, result.lng, result.displayName);
    } catch (e) {
      console.warn('[PostShift] geocode failed:', e.message);
      setGeocodeErr('Could not find this pincode. Check and try again.');
      setLocMode('geocode_err');
    }
  }

  function handleChange(e) {
    const { name, value } = e.target;
    setForm((f) => ({ ...f, [name]: value }));
  }

  const locationReady = locMode === 'gps_ok' || locMode === 'geocode_ok';

  async function handleSubmit(e) {
    e.preventDefault();
    setFormError('');

    if (!locationReady || lat === null || lng === null) {
      setFormError('Hospital area is required. Use GPS or enter a pincode.');
      return;
    }

    const shiftStartISO = datetimeLocalToUtcIso(form.shift_start);
    const shiftEndISO   = form.shift_end ? datetimeLocalToUtcIso(form.shift_end) : undefined;

    const payload = {
      role_required:       form.role_required,
      hospital_name:       form.hospital_name.trim() || user?.company_name || 'My Hospital',
      hospital_latitude:   lat,
      hospital_longitude:  lng,
      shift_start:         shiftStartISO,
      urgency:             form.urgency,
      city_id:             'HYD',
      dispatch_radius_km:  parseFloat(form.dispatch_radius_km) || 10,
      idempotency_key:     crypto.randomUUID(),
    };
    if (locMode === 'geocode_ok') {
      const hp = pincode.replace(/\D/g, '');
      if (hp.length === 6) payload.hospital_pincode = hp;
    } else if (locMode === 'gps_ok') {
      const hp = gpsDerivedPincode.replace(/\D/g, '');
      if (hp.length === 6) payload.hospital_pincode = hp;
    }

    if (form.specialty.trim()) payload.specialty  = form.specialty.trim();
    if (shiftEndISO)           payload.shift_end  = shiftEndISO;
    if (form.pay_rate.trim())  payload.pay_rate   = form.pay_rate.trim();
    if (form.notes.trim())     payload.notes      = form.notes.trim();
    const localityFromLabel = (areaLabel || '').split('—')[0].trim();
    if (localityFromLabel) payload.hospital_locality = localityFromLabel;

    console.log('[PostShift] submit urgency=%s role=%s', payload.urgency, payload.role_required);
    mlog('dispatch', 'shift_post_start', { role: payload.role_required, urgency: payload.urgency });
    setSubmitting(true);
    try {
      const res = await api.post('/shifts/', payload);
      const shiftId = res.data?.shift?.id;
      console.log('[PostShift] created shift id:', shiftId);
      mlog('dispatch', 'shift_post_success', { shift_id: shiftId });
      navigate('/recruiter/dashboard', {
        replace: true,
        state: { shiftCreated: true, shiftId: res.data?.shift?.id },
      });
    } catch (err) {
      console.error('[PostShift] error:', err?.response?.status, JSON.stringify(err?.response?.data));
      mlogError('dispatch', 'shift_post_fail', err);
      const raw = err?.response?.data?.detail;
      setFormError(
        typeof raw === 'string' ? raw
          : Array.isArray(raw) ? raw.map((e) => e.msg || String(e)).join('. ')
          : 'Failed to post shift. Check your connection and try again.',
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <MainLayout>
      <div className="max-w-lg mx-auto px-4 py-4 pb-10">

        {/* Header */}
        <div className="flex items-center gap-3 mb-5">
          <button
            onClick={() => navigate('/recruiter/dashboard')}
            className="flex items-center justify-center min-w-[44px] min-h-[44px] -ml-2 text-gray-500 hover:text-gray-800 active:bg-gray-100 transition-colors rounded-xl"
            aria-label="Back to dashboard"
          >
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-6 h-6">
              <path fillRule="evenodd" d="M17 10a.75.75 0 0 1-.75.75H5.612l4.158 3.96a.75.75 0 1 1-1.04 1.08l-5.5-5.25a.75.75 0 0 1 0-1.08l5.5-5.25a.75.75 0 1 1 1.04 1.08L5.612 9.25H16.25A.75.75 0 0 1 17 10Z" clipRule="evenodd" />
            </svg>
          </button>
          <div>
            <h1 className="text-xl font-bold text-gray-900">Post a Shift</h1>
            <p className="text-xs text-gray-500">Staff will be matched and notified automatically</p>
          </div>
        </div>

        {/* ── Location block ──────────────────────────────────────────────────── */}
        <div className="mb-5">
          <label className="block text-sm font-medium text-gray-700 mb-2">Hospital Area *</label>

          {locMode === 'detecting' && (
            <div className="flex items-center gap-2.5 rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-700">
              <span className="w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin shrink-0" />
              Detecting your location…
            </div>
          )}

          {locMode === 'gps_ok' && (
            <div className="rounded-xl border border-green-200 bg-green-50 px-4 py-3 flex flex-col gap-2">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2 text-sm text-green-700">
                  <span>📍</span>
                  <span className="font-medium">Using your current location</span>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setGpsDerivedPincode('');
                    setGpsRevWarn('');
                    setLocMode('pincode_mode');
                    setTimeout(() => pincodeRef.current?.focus(), 100);
                  }}
                  className="text-xs text-green-600 underline whitespace-nowrap"
                >
                  Change
                </button>
              </div>
              {gpsDerivedPincode.length === 6 && (
                <p className="text-xs text-green-800">
                  Area pincode <span className="font-mono font-semibold">{gpsDerivedPincode}</span> detected — matching will prioritise nurses in this area.
                </p>
              )}
              {gpsDerivedPincode.length !== 6 && gpsRevWarn && (
                <p className="text-xs text-amber-700">{gpsRevWarn}</p>
              )}
            </div>
          )}

          {locMode === 'geocode_ok' && (
            <div className="rounded-xl border border-green-200 bg-green-50 px-4 py-3 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2 text-sm text-green-700">
                <span>📍</span>
                <span className="font-medium">{areaLabel}</span>
              </div>
              <button
                type="button"
                onClick={() => { setPincode(''); setAreaLabel(''); setLocMode('pincode_mode'); setTimeout(() => pincodeRef.current?.focus(), 100); }}
                className="text-xs text-green-600 underline whitespace-nowrap"
              >
                Change
              </button>
            </div>
          )}

          {(locMode === 'pincode_mode' || locMode === 'geocode_err') && (
            <div className="flex flex-col gap-2">
              {locErr && (
                <p className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                  {locErr}
                </p>
              )}
              <div className="flex gap-2">
                <input
                  ref={pincodeRef}
                  type="text"
                  inputMode="numeric"
                  maxLength={6}
                  placeholder="Enter area pincode (e.g. 500032)"
                  value={pincode}
                  onChange={(e) => { setPincode(e.target.value.replace(/\D/g, '')); setGeocodeErr(''); }}
                  onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), handlePincodeConfirm())}
                  className="flex-1 border border-gray-300 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
                <button
                  type="button"
                  onClick={handlePincodeConfirm}
                  disabled={pincode.length < 6}
                  className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 text-white font-semibold px-4 rounded-xl text-sm transition-colors"
                >
                  Confirm
                </button>
              </div>
              <button
                type="button"
                onClick={tryGPS}
                className="text-xs text-indigo-600 hover:underline text-left"
              >
                📍 Use my current location instead
              </button>
              {showLocSettings && (
                <button
                  type="button"
                  onClick={() => openAppSettings()}
                  className="text-xs text-indigo-700 font-semibold text-left"
                >
                  Open app settings to enable location
                </button>
              )}
              {geocodeErr && (
                <p className="text-xs text-red-600">{geocodeErr}</p>
              )}
            </div>
          )}

          {locMode === 'geocoding' && (
            <div className="flex items-center gap-2.5 rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-3 text-sm text-indigo-700">
              <span className="w-4 h-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin shrink-0" />
              Looking up pincode {pincode}…
            </div>
          )}
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-5">

          {/* Who do you need? */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">Who do you need? *</label>
            <select
              name="role_required" value={form.role_required} onChange={handleChange}
              className="w-full border border-gray-300 rounded-xl px-3 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white"
            >
              {ROLES.map((r) => (
                <option key={r.value} value={r.value}>{r.label}</option>
              ))}
            </select>
          </div>

          {/* Specialty */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">
              Department / Specialty <span className="text-gray-400 font-normal">(optional)</span>
            </label>
            <input
              type="text" name="specialty" value={form.specialty} onChange={handleChange}
              placeholder="e.g. Cardiac ICU, Paediatrics, OPD"
              className="w-full border border-gray-300 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>

          {/* Hospital name */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">Hospital / Clinic Name *</label>
            <input
              type="text" name="hospital_name" value={form.hospital_name} onChange={handleChange}
              placeholder="Name as it should appear to staff"
              className="w-full border border-gray-300 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              required
            />
          </div>

          {/* How soon? (urgency) */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">How soon do you need them? *</label>
            <div className="grid grid-cols-2 gap-2">
              {URGENCY.map((u) => (
                <label
                  key={u.value}
                  className={`flex flex-col gap-1 rounded-xl border-2 px-3 py-3 cursor-pointer transition-all ${
                    form.urgency === u.value
                      ? `${u.border} ${u.bg}`
                      : 'border-gray-200 bg-white hover:border-gray-300'
                  }`}
                >
                  <input
                    type="radio" name="urgency" value={u.value}
                    checked={form.urgency === u.value} onChange={handleChange}
                    className="sr-only"
                  />
                  <span className="text-lg leading-none">{u.icon}</span>
                  <span className={`text-xs font-semibold leading-tight ${form.urgency === u.value ? u.title_color : 'text-gray-700'}`}>
                    {u.title}
                  </span>
                  <span className="text-xs text-gray-500 leading-snug">{u.desc}</span>
                </label>
              ))}
            </div>
          </div>

          {/* Shift start */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">Shift Starts At *</label>
            <input
              type="datetime-local" name="shift_start" value={form.shift_start} onChange={handleChange}
              className="w-full border border-gray-300 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              required
            />
          </div>

          {/* Shift end */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">
              Shift Ends At <span className="text-gray-400 font-normal">(optional)</span>
            </label>
            <input
              type="datetime-local" name="shift_end" value={form.shift_end} onChange={handleChange}
              className="w-full border border-gray-300 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>

          {/* Pay */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">
              Pay Offered <span className="text-gray-400 font-normal">(optional)</span>
            </label>
            <input
              type="text" name="pay_rate" value={form.pay_rate} onChange={handleChange}
              placeholder="e.g. ₹800/hr or ₹4,000 for the shift"
              className="w-full border border-gray-300 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>

          {/* Search range (formerly dispatch radius) */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">
              Search Range — <span className="text-indigo-600 font-semibold">{form.dispatch_radius_km} km from hospital</span>
            </label>
            <input
              type="range" name="dispatch_radius_km"
              min="1" max="30" step="1"
              value={form.dispatch_radius_km} onChange={handleChange}
              className="w-full accent-indigo-600"
            />
            <div className="flex justify-between text-xs text-gray-400 mt-1">
              <span>Nearby (1 km)</span>
              <span>Wide area (30 km)</span>
            </div>
          </div>

          {/* Notes */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">
              Additional Notes <span className="text-gray-400 font-normal">(optional)</span>
            </label>
            <textarea
              name="notes" value={form.notes} onChange={handleChange}
              placeholder="Special skills needed, dress code, entry instructions…"
              rows={3}
              className="w-full border border-gray-300 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none"
            />
          </div>

          {formError && (
            <p className="text-sm text-red-600 bg-red-50 border border-red-200 px-3 py-2.5 rounded-xl">{formError}</p>
          )}

          <button
            type="submit"
            disabled={submitting || locMode === 'detecting' || locMode === 'geocoding'}
            className="w-full bg-green-600 hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold py-4 rounded-xl text-sm transition-colors"
          >
            {submitting ? 'Posting…' : '⚡ Find Staff Now'}
          </button>

          <button
            type="button"
            onClick={() => navigate('/recruiter/dashboard')}
            className="w-full text-sm text-gray-400 hover:text-gray-600 py-2 transition-colors"
          >
            Cancel
          </button>

        </form>
      </div>
    </MainLayout>
  );
}
