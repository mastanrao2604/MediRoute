import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api/axios';
import MainLayout from '../layouts/MainLayout';
import Spinner from '../components/Spinner';
import { useAuth } from '../context/AuthContext';

// ── Constants ────────────────────────────────────────────────────────────────
const JOB_TYPE_OPTIONS = [
  { value: 'india',  label: 'India Only', desc: 'Jobs within India',    color: 'border-green-400 bg-green-50 text-green-700' },
  { value: 'abroad', label: 'Abroad Only', desc: 'International jobs',  color: 'border-blue-400 bg-blue-50 text-blue-700' },
  { value: 'both',   label: 'Both',        desc: 'Open to any location', color: 'border-amber-400 bg-amber-50 text-amber-700' },
];
const PASSPORT_OPTIONS = [
  { value: 'yes',     label: 'Yes' },
  { value: 'no',      label: 'No' },
  { value: 'unknown', label: 'Not Sure' },
];

const EMPTY_PROFILE_FORM = { experience_years: '', education: '', skills: '', current_location: '' };
const EMPTY_PREFS_FORM   = { job_type: 'india', preferred_country: '', passport_status: 'unknown' };

function profileToForm(d) {
  return {
    experience_years: d.experience_years ?? '',
    education:        d.education        ?? '',
    skills:           d.skills           ?? '',
    current_location: d.current_location ?? '',
  };
}
function prefsToForm(d) {
  return {
    job_type:          d.job_type          || 'india',
    preferred_country: d.preferred_country || '',
    passport_status:   d.passport_status   || 'unknown',
  };
}

// ── Component ────────────────────────────────────────────────────────────────
export default function Profile() {
  const navigate = useNavigate();
  const { user } = useAuth();

  const [profile,       setProfile]       = useState(null);
  const [preferences,   setPreferences]   = useState(null);
  const [isEditMode,    setIsEditMode]    = useState(false);
  const [form,          setForm]          = useState(EMPTY_PROFILE_FORM);
  const [prefsForm,     setPrefsForm]     = useState(EMPTY_PREFS_FORM);
  const [fetching,      setFetching]      = useState(false);  // start false — shell renders immediately
  const [loading,       setLoading]       = useState(false);
  const [error,         setError]         = useState('');
  const [success,       setSuccess]       = useState('');

  // ── Resume upload state ───────────────────────────────────────────────────
  const [hasResume,    setHasResume]    = useState(false);
  const [uploading,    setUploading]    = useState(false);
  const [uploadError,  setUploadError]  = useState('');
  const [selectedFile, setSelectedFile] = useState(null);
  const fileInputRef = useRef(null);

  // ── Fetch both on mount ───────────────────────────────────────────────────
  async function fetchAll() {
    setFetching(true);
    try {
      const [profileRes, prefsRes] = await Promise.allSettled([
        api.get('/profile/me'),
        api.get('/preferences/me'),
      ]);

      if (profileRes.status === 'fulfilled') {
        setProfile(profileRes.value.data);
        setForm(profileToForm(profileRes.value.data));
        setIsEditMode(false);
      } else if (profileRes.reason?.response?.status === 404) {
        setProfile(null);
        setForm(EMPTY_PROFILE_FORM);
        setIsEditMode(true); // new user → go straight to form
      } else {
        setError('Failed to load profile. Please refresh.');
      }

      if (prefsRes.status === 'fulfilled') {
        setPreferences(prefsRes.value.data);
        setPrefsForm(prefsToForm(prefsRes.value.data));
      }
      // 404 on preferences is fine — user just hasn't set them yet
    } finally {
      setFetching(false);
    }
  }

  useEffect(() => { fetchAll(); }, []);

  // ── Resume helpers ────────────────────────────────────────────────────────
  async function fetchResumeStatus() {
    try {
      const res = await api.get('/resume/me');
      setHasResume(res.data.has_resume === true);
    } catch {
      setHasResume(false);
    }
  }

  useEffect(() => { fetchResumeStatus(); }, []);

  function handleFileChange(e) {
    const f = e.target.files?.[0];
    if (!f) return;
    setUploadError('');
    if (!f.name.toLowerCase().endsWith('.pdf')) {
      setUploadError('Only PDF files are accepted.');
      setSelectedFile(null);
      return;
    }
    if (f.size > 5 * 1024 * 1024) {
      setUploadError('File too large. Maximum size is 5 MB.');
      setSelectedFile(null);
      return;
    }
    setSelectedFile(f);
  }

  async function handleResumeUpload() {
    if (!selectedFile) return;
    setUploading(true);
    setUploadError('');
    const formData = new FormData();
    formData.append('file', selectedFile);
    try {
      await api.post('/resume/upload', formData);
      setHasResume(true);
      setSelectedFile(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
      setSuccess('Resume uploaded successfully!');
      setTimeout(() => setSuccess(''), 3000);
    } catch (err) {
      setUploadError(err.response?.data?.detail || 'Upload failed. Please try again.');
    } finally {
      setUploading(false);
    }
  }

  async function handleViewResume() {
    setUploadError('');
    try {
      const res = await api.get('/resume/me/file', { responseType: 'blob' });
      const blob = new Blob([res.data], { type: 'application/pdf' });
      // Android WebView blocks window.open() — use Web Share API when available.
      const safeFirst = ((user?.name || '').trim().split(/\s+/)[0] || 'user')
        .toLowerCase().replace(/[^a-z0-9-]/g, '') || 'user';
      const dlName = `${safeFirst}_resume.pdf`;
      if (typeof navigator.canShare === 'function') {
        const file = new File([blob], dlName, { type: 'application/pdf' });
        if (navigator.canShare({ files: [file] })) {
          await navigator.share({ files: [file], title: 'My Resume' });
          return;
        }
      }
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank');
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (err) {
      if (err?.name === 'AbortError') return;  // user dismissed share sheet
      setUploadError('Could not open resume. Please try again.');
    }
  }

  async function handleDeleteResume() {
    if (!window.confirm('Delete your uploaded resume? This cannot be undone.')) return;
    setUploadError('');
    try {
      await api.delete('/resume/me/file');
      setHasResume(false);
      setSelectedFile(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
      setSuccess('Resume deleted.');
      setTimeout(() => setSuccess(''), 2500);
    } catch (err) {
      setUploadError(err.response?.data?.detail || 'Failed to delete resume.');
    }
  }

  // ── Handlers ─────────────────────────────────────────────────────────────
  function handleChange(e) {
    setForm((f) => ({ ...f, [e.target.name]: e.target.value }));
  }

  function handlePrefsChange(field, value) {
    setPrefsForm((f) => ({ ...f, [field]: value }));
  }

  function handleEdit() {
    setForm(profileToForm(profile));
    setPrefsForm(preferences ? prefsToForm(preferences) : EMPTY_PREFS_FORM);
    setError('');
    setSuccess('');
    setIsEditMode(true);
  }

  function handleCancel() {
    setError('');
    setIsEditMode(false);
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError('');
    setSuccess('');

    // Validate profile fields
    const expNum = form.experience_years === '' ? null : Number(form.experience_years);
    if (expNum === null || expNum < 0) {
      setError('Years of experience is required and must be 0 or greater.');
      return;
    }
    if (!form.skills.trim()) {
      setError('Skills must not be empty.');
      return;
    }

    setLoading(true);
    try {
      const profilePayload = {
        ...form,
        experience_years: Number(form.experience_years),
        skills:           form.skills.trim(),
        education:        form.education.trim()        || null,
        current_location: form.current_location.trim() || null,
      };
      const prefsPayload = {
        ...prefsForm,
        preferred_country: prefsForm.preferred_country.trim() || null,
      };

      // Save profile (create or update)
      if (profile) {
        await api.put('/profile/me', profilePayload);
      } else {
        await api.post('/profile', profilePayload);
      }

      // Save preferences (upsert — always POST, backend handles create-or-update)
      try {
        await api.post('/preferences', prefsPayload);
      } catch {
        // preferences failure is non-blocking; profile was already saved
      }

      setSuccess('Profile saved!');
      await fetchAll();
      setTimeout(() => {
        setSuccess('');
        navigate('/jobs');
      }, 1200);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to save profile.');
    } finally {
      setLoading(false);
    }
  }

  // ── Loading spinner ───────────────────────────────────────────────────────
  if (fetching) {
    return (
      <MainLayout>
        <div className="flex justify-center py-20"><Spinner /></div>
      </MainLayout>
    );
  }

  // — removed: shell always renders; data loads inline below —

  const pageTitle    = !profile ? 'Complete Your Profile' : isEditMode ? 'Edit Profile' : 'Your Profile';
  const pageSubtitle = !profile ? 'Fill in your details to get better job matches'
                     : isEditMode ? 'Update your profile information'
                     : 'Your saved profile and preferences';
  const viewMode = profile && !isEditMode;

  return (
    <MainLayout>
      <div className="max-w-lg mx-auto px-4 py-6">

        {/* Header */}
        <div className="mb-6 flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">{pageTitle}</h1>
            <p className="text-sm text-gray-500 mt-1">{pageSubtitle}</p>
          </div>
          {viewMode && (
            <button
              onClick={handleEdit}
              className="text-sm text-indigo-600 hover:text-indigo-800 font-medium border border-indigo-200 px-4 py-2 rounded-xl transition-colors"
            >
              Edit Profile
            </button>
          )}
        </div>

        {error   && <p className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded-lg mb-4">{error}</p>}
        {success && <p className="text-sm text-green-700 bg-green-50 px-3 py-2 rounded-lg mb-4">{success}</p>}

        {/* ── RESUME SECTION (always visible) ──────────────────────────── */}
        <section className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5 mb-0">
          <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-4">Resume</h2>

          {hasResume ? (
            <div className="flex flex-col gap-3">
              <div className="flex items-center gap-2.5 bg-green-50 border border-green-200 rounded-xl px-4 py-3">
                <svg className="w-5 h-5 text-green-600 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                    d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                <span className="text-sm font-medium text-green-800">Resume uploaded ✓</span>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={handleViewResume}
                  className="flex-1 text-sm font-medium text-indigo-700 bg-indigo-50 hover:bg-indigo-100 border border-indigo-200 py-3 rounded-xl transition-colors"
                >
                  View PDF
                </button>
                <button
                  onClick={handleDeleteResume}
                  className="flex-1 text-sm font-medium text-red-600 bg-red-50 hover:bg-red-100 border border-red-200 py-3 rounded-xl transition-colors"
                >
                  Delete
                </button>
              </div>
              {/* Allow replacing */}
              <div>
                <p className="text-xs text-gray-400 mb-1.5">Replace resume:</p>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="application/pdf,.pdf"
                  onChange={handleFileChange}
                  className="block w-full text-sm text-gray-600 file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-indigo-50 file:text-indigo-700 hover:file:bg-indigo-100 cursor-pointer"
                />
                {selectedFile && (
                  <button
                    onClick={handleResumeUpload}
                    disabled={uploading}
                    className="mt-2 w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-semibold py-2.5 rounded-xl transition-colors"
                  >
                    {uploading ? 'Uploading…' : `Upload "${selectedFile.name}"`}
                  </button>
                )}
              </div>
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              <input
                ref={fileInputRef}
                type="file"
                accept="application/pdf,.pdf"
                onChange={handleFileChange}
                className="block w-full text-sm text-gray-600 file:mr-3 file:py-2.5 file:px-4 file:rounded-xl file:border-0 file:text-sm file:font-semibold file:bg-indigo-600 file:text-white hover:file:bg-indigo-700 cursor-pointer"
              />
              {selectedFile && (
                <p className="text-xs text-gray-500">Selected: <span className="font-medium">{selectedFile.name}</span> ({(selectedFile.size / 1024).toFixed(0)} KB)</p>
              )}
              {selectedFile && (
                <button
                  onClick={handleResumeUpload}
                  disabled={uploading}
                  className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white font-semibold py-3 rounded-xl transition-colors text-sm"
                >
                  {uploading ? (
                    <span className="flex items-center justify-center gap-2">
                      <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                      Uploading…
                    </span>
                  ) : 'Upload Resume'}
                </button>
              )}
              {!selectedFile && (
                <p className="text-xs text-gray-400 text-center">PDF only · Max 5 MB</p>
              )}
            </div>
          )}

          {uploadError && (
            <p className="text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2 mt-2">{uploadError}</p>
          )}
        </section>

        {/* ── VIEW MODE ─────────────────────────────────────────────────── */}
        {viewMode && (
          <div className="flex flex-col gap-4">
            {/* Professional Details */}
            <section className="bg-white rounded-2xl border border-gray-100 shadow-sm p-6">
              <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-4">Professional Details</h2>
              <div className="flex flex-col gap-3">
                <ProfileField label="Years of Experience" value={profile.experience_years ?? '—'} />
                <ProfileField label="Skills"              value={profile.skills           ?? '—'} />
                {profile.education        && <ProfileField label="Education" value={profile.education} />}
                {profile.current_location && <ProfileField label="Location"  value={profile.current_location} />}
              </div>
            </section>

            {/* Job Preferences */}
            <section className="bg-white rounded-2xl border border-gray-100 shadow-sm p-6">
              <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-4">Job Preferences</h2>
              {preferences ? (
                <div className="flex flex-col gap-3">
                  <ProfileField label="Job Type"       value={JOB_TYPE_OPTIONS.find(o => o.value === preferences.job_type)?.label ?? preferences.job_type} />
                  {preferences.preferred_country && <ProfileField label="Preferred Country" value={preferences.preferred_country} />}
                  <ProfileField label="Has Passport"   value={PASSPORT_OPTIONS.find(o => o.value === preferences.passport_status)?.label ?? '—'} />
                </div>
              ) : (
                <p className="text-sm text-gray-400">No preferences set yet.</p>
              )}
            </section>
          </div>
        )}

        {/* ── CREATE / EDIT FORM ────────────────────────────────────────── */}
        {!viewMode && (
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            {/* Section 1 — Professional Details */}
            <section className="bg-white rounded-2xl border border-gray-100 shadow-sm p-6 flex flex-col gap-5">
              <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide">Professional Details</h2>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1.5">
                  Years of Experience <span className="text-red-500">*</span>
                </label>
                <input
                  type="number"
                  name="experience_years"
                  value={form.experience_years}
                  onChange={handleChange}
                  min={0} max={60}
                  placeholder="e.g. 3"
                  className="w-full border border-gray-300 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1.5">
                  Skills <span className="text-red-500">*</span>
                </label>
                <input
                  type="text"
                  name="skills"
                  value={form.skills}
                  onChange={handleChange}
                  placeholder="e.g. ICU, Ventilator, Patient Care"
                  className="w-full border border-gray-300 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition"
                />
                <p className="text-xs text-gray-400 mt-1">Separate with commas</p>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1.5">Education</label>
                <textarea
                  name="education"
                  value={form.education}
                  onChange={handleChange}
                  rows={2}
                  placeholder="e.g. B.Sc Nursing, XYZ University"
                  className="w-full border border-gray-300 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition resize-none"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1.5">Current Location</label>
                <input
                  type="text"
                  name="current_location"
                  value={form.current_location}
                  onChange={handleChange}
                  placeholder="e.g. Mumbai"
                  className="w-full border border-gray-300 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition"
                />
              </div>
            </section>

            {/* Section 2 — Job Preferences */}
            <section className="bg-white rounded-2xl border border-gray-100 shadow-sm p-6 flex flex-col gap-5">
              <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide">Job Preferences</h2>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-3">Job Location Preference</label>
                <div className="grid grid-cols-3 gap-3">
                  {JOB_TYPE_OPTIONS.map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => handlePrefsChange('job_type', opt.value)}
                      className={`border-2 rounded-xl p-3 text-left transition-all ${
                        prefsForm.job_type === opt.value
                          ? opt.color
                          : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
                      }`}
                    >
                      <p className="text-sm font-semibold">{opt.label}</p>
                      <p className="text-xs mt-0.5 opacity-75">{opt.desc}</p>
                    </button>
                  ))}
                </div>
              </div>

              {(prefsForm.job_type === 'abroad' || prefsForm.job_type === 'both') && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">Preferred Country</label>
                  <input
                    type="text"
                    value={prefsForm.preferred_country}
                    onChange={(e) => handlePrefsChange('preferred_country', e.target.value)}
                    placeholder="e.g. UAE, Germany, Australia"
                    className="w-full border border-gray-300 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition"
                  />
                </div>
              )}

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-3">Do you have a passport?</label>
                <div className="flex gap-3">
                  {PASSPORT_OPTIONS.map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => handlePrefsChange('passport_status', opt.value)}
                      className={`flex-1 border-2 rounded-xl py-2.5 text-sm font-medium transition-all ${
                        prefsForm.passport_status === opt.value
                          ? 'border-indigo-500 bg-indigo-50 text-indigo-700'
                          : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
                      }`}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>
            </section>

            {/* Action buttons */}
            <div className="flex gap-3">
              {profile && (
                <button
                  type="button"
                  onClick={handleCancel}
                  disabled={loading}
                  className="flex-1 border border-gray-300 hover:bg-gray-50 disabled:opacity-60 text-gray-700 font-semibold py-3 rounded-xl transition-colors"
                >
                  Cancel
                </button>
              )}
              <button
                type="submit"
                disabled={loading}
                className="flex-1 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white font-semibold py-3 rounded-xl transition-colors"
              >
                {loading ? 'Saving…' : 'Save Profile'}
              </button>
            </div>
          </form>
        )}
      </div>
    </MainLayout>
  );
}

function ProfileField({ label, value }) {
  return (
    <div>
      <p className="text-xs font-medium text-gray-400 uppercase tracking-wide mb-0.5">{label}</p>
      <p className="text-sm text-gray-800">{value}</p>
    </div>
  );
}
