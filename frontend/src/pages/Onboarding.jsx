import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api/axios';
import { useAuth } from '../context/AuthContext';

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
  { value: 'driver',          label: 'Medical Driver' },
  { value: 'front_office',    label: 'Other Healthcare Staff' },
  { value: 'recruiter',       label: 'Recruiter / Hospital HR' },
];

const EXP_OPTIONS = [
  { value: 0,  label: 'Fresher' },
  { value: 1,  label: '1–2 yrs' },
  { value: 3,  label: '3–5 yrs' },
  { value: 6,  label: '6–9 yrs' },
  { value: 10, label: '10+ yrs' },
];

const JOB_TYPE_OPTIONS = [
  { value: 'india',  label: '🇮🇳 India' },
  { value: 'abroad', label: '✈️ Abroad' },
  { value: 'both',   label: '🌍 Both' },
];

const PASSPORT_OPTIONS = [
  { value: 'yes',     label: 'Yes' },
  { value: 'no',      label: 'No' },
  { value: 'unknown', label: 'Not sure' },
];

const COUNTRY_CHIPS = [
  { value: 'germany', label: '🇩🇪 Germany' },
  { value: 'uae',     label: '🇦🇪 UAE' },
  { value: 'others',  label: '🌐 Others' },
];

// Map a saved preferred_country string back to a chip value
function countryToChip(saved) {
  if (!saved) return '';
  const lower = saved.toLowerCase();
  if (lower === 'germany') return 'germany';
  if (lower === 'uae')     return 'uae';
  return 'others';
}

// Map chip → display value sent to the backend
function chipToCountry(chip, custom) {
  if (chip === 'germany') return 'Germany';
  if (chip === 'uae')     return 'UAE';
  if (chip === 'others')  return custom.trim();
  return '';
}

// Whether a role value is a job-seeker (not recruiter)
const isJobSeeker = (r) => r && r !== 'recruiter';

export default function Onboarding() {
  const navigate = useNavigate();
  const { login, token, user } = useAuth();
  const detailsRef = useRef(null);

  // ── Basic identity ────────────────────────────────────────────────────────
  const [name, setName] = useState('');
  const [role, setRole] = useState('');

  // ── Professional details (job seeker only) ────────────────────────────────
  const [expYears,         setExpYears]         = useState(null);
  const [city,             setCity]             = useState('');
  const [skills,           setSkills]           = useState('');
  const [jobType,          setJobType]          = useState('');
  const [passport,         setPassport]         = useState('unknown');
  const [countryChip,      setCountryChip]      = useState('');   // 'germany' | 'uae' | 'others' | ''
  const [customCountry,    setCustomCountry]    = useState('');   // free-text when chip = 'others'

  // ── UI state ──────────────────────────────────────────────────────────────
  const [loading,    setLoading]    = useState(false);
  const [prefilling, setPrefilling] = useState(false);
  const [error,      setError]      = useState('');

  // ── On mount: pre-fill if returning user ─────────────────────────────────
  useEffect(() => {
    let effectiveUser = user;
    if (!effectiveUser) {
      try { effectiveUser = JSON.parse(localStorage.getItem('mediroute_user')); } catch { /* ignore */ }
    }
    if (effectiveUser?.name) setName(effectiveUser.name);
    if (effectiveUser?.role) setRole(effectiveUser.role);

    // If already has role and is a job-seeker, pre-fill profile details too
    if (effectiveUser?.role && isJobSeeker(effectiveUser.role)) {
      prefillDetails();
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Pre-fill from existing partial profile data ───────────────────────────
  async function prefillDetails() {
    setPrefilling(true);
    try {
      const [profileRes, prefsRes] = await Promise.allSettled([
        api.get('/profile/me'),
        api.get('/preferences/me'),
      ]);
      if (profileRes.status === 'fulfilled') {
        const p = profileRes.value.data;
        if (p.experience_years != null) setExpYears(p.experience_years);
        if (p.current_location)         setCity(p.current_location);
        if (p.skills)                   setSkills(p.skills);
      }
      if (prefsRes.status === 'fulfilled') {
        const pr = prefsRes.value.data;
        setJobType(pr.job_type          || '');
        setPassport(pr.passport_status  || 'unknown');
        const saved = pr.preferred_country || '';
        const chip  = countryToChip(saved);
        setCountryChip(chip);
        if (chip === 'others') setCustomCountry(saved);
      }
    } catch { /* empty form is fine */ } finally {
      setPrefilling(false);
    }
  }

  // ── When role changes: trigger prefill + smooth scroll ───────────────────
  function handleRoleChange(newRole) {
    setRole(newRole);
    setError('');
    if (isJobSeeker(newRole)) {
      prefillDetails();
      // Scroll to the expanded section after a brief render delay
      setTimeout(() => {
        detailsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }, 120);
    }
  }

  // ── Submit ────────────────────────────────────────────────────────────────
  async function handleSubmit(e) {
    e.preventDefault();
    if (!name.trim() || !role) { setError('Please enter your name and select a role.'); return; }

    if (isJobSeeker(role) && !city.trim()) { setError('Please enter your current city.'); return; }

    if (isJobSeeker(role) && !jobType) { setError('Please select your preferred job location.'); return; }

    if (isJobSeeker(role) && (expYears === null || !skills.trim())) {
      setError('Please select your experience level and add at least one skill.');
      return;
    }

    setLoading(true);
    setError('');

    try {
      // 1. Save name + role
      const userRes = await api.post('/user/onboarding', { name: name.trim(), role });
      login(token, userRes.data);

      if (role === 'recruiter') {
        navigate(userRes.data.company_name ? '/recruiter/dashboard' : '/recruiter/onboarding', { replace: true });
        return;
      }

      // 2. Save profile details
      const profileData = {
        experience_years: expYears,
        skills: skills.trim(),
        current_location: city.trim() || undefined,
      };
      try {
        await api.put('/profile/me', profileData);
      } catch (err) {
        if (err.response?.status === 404) await api.post('/profile/', profileData);
        else throw err;
      }

      // 3. Validate country if abroad/both selected
      if (['abroad', 'both'].includes(jobType) && countryChip === 'others' && !customCountry.trim()) {
        setError('Please enter your preferred country.');
        setLoading(false);
        return;
      }

      // 4. Save preferences
      const computedCountry = ['abroad', 'both'].includes(jobType) ? chipToCountry(countryChip, customCountry) : undefined;
      await api.post('/preferences/', {
        job_type:          jobType,
        passport_status:   passport,
        preferred_country: computedCountry || undefined,
      });

      // 4. Refresh user (profile_complete is now true)
      const meRes = await api.get('/auth/me');
      login(token, meRes.data);
      navigate('/dashboard', { replace: true });

    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to save. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  // ── Chip button ───────────────────────────────────────────────────────────
  function Chip({ active, onClick, children }) {
    return (
      <button
        type="button"
        onClick={onClick}
        className={`px-3 py-2 rounded-lg text-sm font-medium border transition-all ${
          active
            ? 'bg-indigo-600 border-indigo-600 text-white'
            : 'border-gray-300 text-gray-600 hover:border-indigo-400 hover:bg-indigo-50'
        }`}
      >
        {children}
      </button>
    );
  }

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-gradient-to-b from-indigo-50 to-white px-4 py-8">
      <div className="w-full max-w-sm mx-auto">

        {/* Logo */}
        <div className="text-center mb-6">
          <div className="inline-flex items-center gap-0.5 mb-1">
            <span className="text-indigo-600 font-extrabold text-3xl">Medi</span>
            <span className="text-green-500 font-extrabold text-3xl">Route</span>
          </div>
          <p className="text-gray-400 text-xs font-medium">Complete your profile to get started</p>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">

          {/* ── Section 1: Identity ── */}
          <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-5">
            <h2 className="text-base font-bold text-gray-900 mb-4">Who are you?</h2>

            <div className="flex flex-col gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1.5">Full Name <span className="text-red-500">*</span></label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Priya Sharma"
                  className="w-full border border-gray-300 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1.5">Healthcare Role <span className="text-red-500">*</span></label>
                <div className="flex flex-wrap gap-2">
                  {ROLES.map((r) => (
                    <button
                      key={r.value}
                      type="button"
                      onClick={() => handleRoleChange(r.value)}
                      className={`px-3 py-2 rounded-xl text-sm font-medium border transition-all ${
                        role === r.value
                          ? 'bg-indigo-600 border-indigo-600 text-white'
                          : 'border-gray-200 text-gray-600 hover:border-indigo-400 hover:bg-indigo-50'
                      }`}
                    >
                      {r.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* ── Section 2: Professional Details — expands for job seekers ── */}
          {isJobSeeker(role) && (
            <div
              ref={detailsRef}
              className="flex flex-col gap-4 animate-in"
              style={{ animation: 'fadeSlideIn 0.25s ease-out' }}
            >
              {/* Professional Details */}
              <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-5">
                <div className="flex items-center gap-2 mb-4">
                  <div className="w-1 h-5 bg-indigo-600 rounded-full" />
                  <h3 className="text-base font-bold text-gray-900">Professional Details</h3>
                </div>

                {prefilling ? (
                  <div className="flex justify-center py-8">
                    <div className="w-7 h-7 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin" />
                  </div>
                ) : (
                  <div className="flex flex-col gap-4">
                    {/* Experience */}
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-2">Years of Experience <span className="text-red-500">*</span></label>
                      <div className="flex flex-wrap gap-2">
                        {EXP_OPTIONS.map((opt) => (
                          <Chip key={opt.value} active={expYears === opt.value} onClick={() => setExpYears(opt.value)}>
                            {opt.label}
                          </Chip>
                        ))}
                      </div>
                    </div>

                    {/* Current City */}
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1.5">Current City <span className="text-red-500">*</span></label>
                      <input
                        type="text"
                        value={city}
                        onChange={(e) => setCity(e.target.value)}
                        placeholder="e.g. Mumbai, Hyderabad"
                        className="w-full border border-gray-300 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition"
                      />
                    </div>

                    {/* Skills */}
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1.5">
                        Skills / Specialization <span className="text-red-500">*</span>
                      </label>
                      <input
                        type="text"
                        value={skills}
                        onChange={(e) => setSkills(e.target.value)}
                        placeholder="e.g. ICU, Dialysis, Pediatrics, OT"
                        className="w-full border border-gray-300 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition"
                      />
                      <p className="text-xs text-gray-400 mt-1">Separate with commas</p>
                    </div>
                  </div>
                )}
              </div>

              {/* Job Preferences */}
              {!prefilling && (
                <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-5">
                  <div className="flex items-center gap-2 mb-4">
                    <div className="w-1 h-5 bg-green-500 rounded-full" />
                    <h3 className="text-base font-bold text-gray-900">Job Preferences</h3>
                  </div>

                  <div className="flex flex-col gap-4">
                    {/* Preferred Job Location */}
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-2">Preferred Job Location <span className="text-red-500">*</span></label>
                      <div className="flex gap-2">
                        {JOB_TYPE_OPTIONS.map((opt) => (
                          <button
                            key={opt.value}
                            type="button"
                            onClick={() => {
                              setJobType(opt.value);
                              if (opt.value === 'india') {
                                setPassport('unknown');
                                setCountryChip('');
                                setCustomCountry('');
                              }
                            }}
                            className={`flex-1 py-2.5 rounded-xl text-sm font-medium border transition-all ${
                              jobType === opt.value
                                ? 'bg-indigo-600 border-indigo-600 text-white'
                                : 'border-gray-300 text-gray-600 hover:border-indigo-400'
                            }`}
                          >
                            {opt.label}
                          </button>
                        ))}
                      </div>
                    </div>

                    {/* Passport + Country — only for abroad/both */}
                    {['abroad', 'both'].includes(jobType) && (
                      <>
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-2">Do you have a passport?</label>
                          <div className="flex gap-2">
                            {PASSPORT_OPTIONS.map((opt) => (
                              <Chip key={opt.value} active={passport === opt.value} onClick={() => setPassport(opt.value)}>
                                {opt.label}
                              </Chip>
                            ))}
                          </div>
                        </div>

                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-2">
                            Preferred Country
                          </label>
                          <div className="flex gap-2 flex-wrap">
                            {COUNTRY_CHIPS.map((c) => (
                              <Chip
                                key={c.value}
                                active={countryChip === c.value}
                                onClick={() => {
                                  setCountryChip(c.value);
                                  if (c.value !== 'others') setCustomCountry('');
                                }}
                              >
                                {c.label}
                              </Chip>
                            ))}
                          </div>

                          {countryChip === 'others' && (
                            <input
                              type="text"
                              value={customCountry}
                              onChange={(e) => setCustomCountry(e.target.value)}
                              placeholder="e.g. Saudi Arabia, UK, Canada"
                              className="mt-3 w-full border border-gray-300 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition"
                              autoFocus
                            />
                          )}
                        </div>
                      </>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ── Error ── */}
          {error && (
            <p className="text-sm text-red-600 bg-red-50 border border-red-100 px-3 py-2.5 rounded-xl">{error}</p>
          )}

          {/* ── Submit ── */}
          {role && (
            <div className="pb-6">
              <button
                type="submit"
                disabled={loading}
                className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold py-3.5 rounded-xl transition-colors shadow-sm"
              >
                {loading
                  ? 'Saving…'
                  : role === 'recruiter'
                  ? 'Continue as Recruiter →'
                  : 'Complete Profile →'}
              </button>

              {isJobSeeker(role) && (
                <button
                  type="button"
                  onClick={() => navigate('/dashboard', { replace: true })}
                  className="w-full text-center text-xs text-gray-400 hover:text-gray-600 transition-colors mt-3 py-1"
                >
                  Skip for now
                </button>
              )}
            </div>
          )}

        </form>
      </div>

      {/* ── Inline CSS animation ── */}
      <style>{`
        @keyframes fadeSlideIn {
          from { opacity: 0; transform: translateY(12px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );

}
