import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api/axios';
import { useAuth } from '../context/AuthContext';
import MainLayout from '../layouts/MainLayout';
import { downloadPDF } from '../utils/downloadPdf';

const EMPTY_FORM = {
  full_name: '',
  email: '',
  phone: '',
  location: '',
  profile_summary: '',
  education: '',
  experience: '',
  skills: '',
  languages: '',
  photo_url: '',
};

export default function ResumeBuilder() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [form, setForm] = useState(EMPTY_FORM);
  const [photoPreview, setPhotoPreview] = useState(null);
  const [photoFile, setPhotoFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [isSaved, setIsSaved] = useState(false);  // true once data has been saved to backend
  const [error, setError] = useState('');
  const [toast, setToast] = useState('');
  const fileInputRef = useRef(null);

  useEffect(() => {
    async function prefill() {
      try {
        const rbRes = await api.get('/resume/builder/me').catch(() => null);
        if (rbRes?.data?.length > 0) {
          const latest = rbRes.data[rbRes.data.length - 1];
          setForm({
            full_name: latest.full_name || '',
            email: latest.email || '',
            phone: latest.phone || '',
            location: latest.location || '',
            profile_summary: latest.profile_summary || '',
            education: latest.education || '',
            experience: latest.experience || '',
            skills: latest.skills || '',
            languages: latest.languages || '',
            photo_url: latest.photo_url || '',
          });
          // Restore photo preview: fetch through the authenticated API endpoint
          // so we always get a valid blob URL regardless of the storage backend
          // (Supabase key vs local path — neither is directly usable as <img> src).
          if (latest.photo_url) {
            api.get('/resume/photo/me', { responseType: 'blob' })
              .then((res) => setPhotoPreview(URL.createObjectURL(res.data)))
              .catch(() => setPhotoPreview(null));
          }
          setIsSaved(true);  // data already exists on backend
          return;
        }
        // Use cached user from AuthContext — avoids an extra /auth/me round-trip.
        const profileRes = await api.get('/profile/me').catch(() => null);
        const profile = profileRes?.data ?? null;
        setForm((f) => ({
          ...f,
          full_name: user?.name || '',
          phone: user?.phone || '',
          location: profile?.current_location || '',
          skills: profile?.skills || '',
          education: profile?.education || '',
        }));
      } finally {
        setLoading(false);
      }
    }
    prefill();
  }, []);

  function handleChange(e) {
    setForm((f) => ({ ...f, [e.target.name]: e.target.value }));
  }

  function handlePhotoChange(e) {
    const file = e.target.files[0];
    if (!file) return;
    const allowed = new Set(['image/jpeg', 'image/png', 'image/webp', 'image/jpg']);
    const allowedExt = new Set(['.jpg', '.jpeg', '.png', '.webp']);
    const ext = (file.name || '').toLowerCase().match(/\.[^.]+$/)?.[0] ?? '';
    // Android file pickers sometimes report application/octet-stream — accept by extension
    if (!allowed.has(file.type) && !allowedExt.has(ext)) {
      setError('Please select an image file (JPEG, PNG, or WebP).');
      return;
    }
    if (file.size > 2 * 1024 * 1024) {
      setError('Image must be under 2 MB.');
      return;
    }
    setPhotoFile(file);
    setPhotoPreview(URL.createObjectURL(file));
    setError('');
  }

  async function handleSave(e) {
    e.preventDefault();
    setError('');
    if (!form.full_name.trim()) { setError('Full name is required.'); return; }
    if (form.phone.replace(/\D/g, '').length < 10) { setError('Phone must be at least 10 digits.'); return; }
    if (!form.skills.trim()) { setError('Skills are required.'); return; }

    setSaving(true);
    try {
      let photoUrl = form.photo_url;
      if (photoFile) {
        try {
          const fd = new FormData();
          fd.append('file', photoFile);
          // No Content-Type header — axios auto-sets multipart/form-data with boundary
          const photoRes = await api.post('/resume/photo', fd, { timeout: 30000, headers: { 'Content-Type': undefined } });
          photoUrl = photoRes.data.photo_url;
          setForm((f) => ({ ...f, photo_url: photoUrl }));
          setPhotoFile(null);
        } catch (photoErr) {
          console.error('[MediRoute] Photo upload error', {
            status: photoErr?.response?.status,
            detail: photoErr?.response?.data?.detail,
            message: photoErr?.message,
          });
          const raw = photoErr?.response?.data?.detail;
          const msg = typeof raw === 'string' ? raw
            : Array.isArray(raw) ? raw.map((e) => {
                const field = Array.isArray(e.loc) ? e.loc.filter((f) => f !== 'body').join('.') : '';
                return field ? `${field}: ${e.msg}` : (e.msg || String(e));
              }).join('. ')
            : (photoErr?.message || 'Photo upload failed.');
          setSaving(false);
          setError(`Photo upload failed: ${msg}`);
          return;
        }
      }
      const payload = { ...form, photo_url: photoUrl };
      await api.post('/resume/builder', payload);
      setIsSaved(true);
      showToast('Resume saved successfully!');
    } catch (err) {
      console.error('[MediRoute] Save error', {
        status: err?.response?.status,
        detail: err?.response?.data?.detail,
        message: err?.message,
      });
      // FastAPI Pydantic v2 validation errors return detail as an array of objects.
      // Rendering an array/object in JSX causes React Error #31 (white screen).
      const raw = err.response?.data?.detail;
      setError(
        typeof raw === 'string'
          ? raw
          : Array.isArray(raw)
            ? raw.map((e) => {
                const field = Array.isArray(e.loc) ? e.loc.filter((f) => f !== 'body').join('.') : '';
                return field ? `${field}: ${e.msg}` : (e.msg || String(e));
              }).join('. ')
            : 'Failed to save resume.',
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleDownloadPDF() {
    setError('');
    setDownloading(true);
    try {
      // Auto-save first so the backend always has the latest data.
      // This means Download PDF works even if user forgot to press Save.
      if (!isIncomplete) {
        let photoUrl = form.photo_url;
        if (photoFile) {
          try {
            const fd = new FormData();
            fd.append('file', photoFile);
            // No Content-Type header — axios auto-sets multipart/form-data with boundary
            const photoRes = await api.post('/resume/photo', fd, { timeout: 30000, headers: { 'Content-Type': undefined } });
            photoUrl = photoRes.data.photo_url;
            setForm((f) => ({ ...f, photo_url: photoUrl }));
            setPhotoFile(null);
          } catch (photoErr) {
            console.error('[MediRoute] Photo upload error (PDF flow)', {
              status: photoErr?.response?.status,
              detail: photoErr?.response?.data?.detail,
              message: photoErr?.message,
            });
            // Continue without photo rather than blocking PDF download
            photoUrl = form.photo_url;
          }
        }
        await api.post('/resume/builder', { ...form, photo_url: photoUrl });
        setIsSaved(true);
      }

      const res = await api.get('/resume/builder/pdf', { responseType: 'blob', timeout: 60000 });
      const blob = new Blob([res.data], { type: 'application/pdf' });
      const safeFirst = ((form.full_name || '').trim().split(/\s+/)[0] || 'user')
        .toLowerCase().replace(/[^a-z0-9-]/g, '') || 'user';
      const fileName = `${safeFirst}_resume.pdf`;
      const { savedTo } = await downloadPDF(blob, fileName);
      if (savedTo === 'downloads') showToast('PDF saved to Downloads!');
      else if (savedTo === 'documents') showToast('PDF saved to app Documents folder.');
      else showToast('PDF downloaded!');
    } catch (err) {
      // User dismissed the native share sheet — not an error.
      if (err?.name === 'AbortError') return;
      // Axios returns a Blob as response.data when responseType='blob' and server errors (4xx/5xx).
      // Extract the JSON detail from it to show a useful message.
      let msg = 'PDF generation failed. Please try again.';
      if (err?.response?.data instanceof Blob) {
        try {
          const text = await err.response.data.text();
          const json = JSON.parse(text);
          if (json.detail) {
            // Pydantic v2 can return detail as an array of {type,loc,msg,input}.
            // Coerce to a string — rendering objects in JSX causes React Error #31.
            const d = json.detail;
            msg = typeof d === 'string'
              ? d
              : Array.isArray(d)
                ? d.map((e) => e.msg || String(e)).join('. ')
                : 'PDF generation failed. Please try again.';
          }
        } catch { /* ignore parse errors — fall through to default msg */ }
      } else if (err?.message) {
        msg = err.message;
      }
      setError(msg);
      console.error('PDF download error:', err?.response?.status, msg);
    } finally {
      setDownloading(false);
    }
  }

  function showToast(msg) {
    setToast(msg);
    setTimeout(() => setToast(''), 3000);
  }

  const isIncomplete =
    !form.full_name.trim() ||
    form.phone.replace(/\D/g, '').length < 10 ||
    !form.skills.trim();

  return (
    <MainLayout>
      {toast && (
        <div className="fixed top-16 left-1/2 -translate-x-1/2 z-50 bg-gray-900 text-white text-sm font-medium px-5 py-2.5 rounded-xl shadow-lg pointer-events-none">
          {toast}
        </div>
      )}
      <div className="max-w-7xl mx-auto px-4 py-6">
        <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Resume Builder</h1>
            <p className="text-sm text-gray-500 mt-1">Edit your details � preview updates live on the right</p>
          </div>
          <div className="flex gap-3">
            <button
              onClick={() => navigate('/dashboard')}
              className="text-sm text-gray-600 hover:text-gray-900 font-medium px-4 py-2.5 rounded-xl border border-gray-200 bg-white transition-colors"
            >
              Back
            </button>
            <button
              onClick={handleDownloadPDF}
              disabled={downloading || isIncomplete}
              title={isIncomplete ? 'Complete Name, Phone, and Skills first' : 'Download as PDF'}
              className="flex items-center gap-2 bg-green-600 hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-semibold px-4 py-2.5 rounded-xl transition-colors"
            >
              <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              {downloading ? 'Generating...' : 'Download PDF'}
            </button>
          </div>
        </div>

        {isIncomplete && (
          <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 mb-5 text-sm text-amber-800">
            Complete <strong>Name</strong>, <strong>Phone</strong>, and <strong>Skills</strong> to enable PDF download.
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
          <form onSubmit={handleSave} className="flex flex-col gap-5">
            <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5">
              <h3 className="text-sm font-semibold text-gray-700 mb-4">Profile Photo</h3>
              <div className="flex items-center gap-5">
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className="w-24 h-24 rounded-full bg-gray-100 border-2 border-dashed border-gray-300 flex items-center justify-center overflow-hidden hover:border-indigo-400 transition-colors shrink-0"
                >
                  {photoPreview ? (
                    <img src={photoPreview} alt="Profile" className="w-full h-full object-cover" />
                  ) : (
                    <svg className="w-8 h-8 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                    </svg>
                  )}
                </button>
                <div>
                  <button type="button" onClick={() => fileInputRef.current?.click()} className="text-sm text-indigo-600 hover:underline font-medium">
                    {photoPreview ? 'Change photo' : 'Upload photo'}
                  </button>
                  <p className="text-xs text-gray-400 mt-1">JPG, PNG, WebP - max 2 MB</p>
                  <p className="text-xs text-gray-400">Appears in the PDF</p>
                </div>
                <input ref={fileInputRef} type="file" accept="image/jpeg,image/png,image/webp" onChange={handlePhotoChange} className="hidden" />
              </div>
            </div>

            <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5">
              <h3 className="text-sm font-semibold text-gray-700 mb-4">Basic Information</h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {[
                  { name: 'full_name', label: 'Full Name', required: true, placeholder: 'Priya Sharma' },
                  { name: 'phone', label: 'Phone', required: true, placeholder: '9876543210' },
                  { name: 'email', label: 'Email', placeholder: 'priya@example.com' },
                  { name: 'location', label: 'Location', placeholder: 'Hyderabad, India' },
                ].map((f) => (
                  <div key={f.name}>
                    <label className="block text-xs font-medium text-gray-600 mb-1.5">
                      {f.label}{f.required && <span className="text-red-500 ml-0.5">*</span>}
                    </label>
                    <input type="text" name={f.name} value={form[f.name]} onChange={handleChange} placeholder={f.placeholder}
                      className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition" />
                  </div>
                ))}
              </div>
            </div>

            <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5">
              <h3 className="text-sm font-semibold text-gray-700 mb-4">Skills &amp; Languages</h3>
              <div className="flex flex-col gap-4">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1.5">
                    Skills <span className="text-red-500">*</span><span className="font-normal text-gray-400 ml-1">comma separated</span>
                  </label>
                  <input type="text" name="skills" value={form.skills} onChange={handleChange} placeholder="Patient Care, IV Insertion, ECG, BLS"
                    className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition" />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1.5">
                    Languages<span className="font-normal text-gray-400 ml-1">comma separated</span>
                  </label>
                  <input type="text" name="languages" value={form.languages} onChange={handleChange} placeholder="English, Hindi, Telugu"
                    className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition" />
                </div>
              </div>
            </div>

            <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5">
              <h3 className="text-sm font-semibold text-gray-700 mb-4">Professional Summary</h3>
              <textarea name="profile_summary" value={form.profile_summary} onChange={handleChange} rows={4}
                placeholder="Write a short paragraph about your professional background and goals..."
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition resize-none" />
            </div>

            <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5">
              <h3 className="text-sm font-semibold text-gray-700 mb-1.5">Work Experience</h3>
              <p className="text-xs text-gray-400 mb-3">Each line becomes a paragraph. Start bullet points with &bull;</p>
              <textarea name="experience" value={form.experience} onChange={handleChange} rows={6}
                placeholder={"Staff Nurse - Apollo Hospitals (2021-Present)\n� ICU patient monitoring and medication administration\n� Managed post-surgical recovery ward"}
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition resize-none" />
            </div>

            <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5">
              <h3 className="text-sm font-semibold text-gray-700 mb-4">Education</h3>
              <textarea name="education" value={form.education} onChange={handleChange} rows={3}
                placeholder={"B.Sc. Nursing - Osmania University (2019)\nDiploma in Critical Care Nursing (2020)"}
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition resize-none" />
            </div>

            {error && (
              <p className="text-sm text-red-600 bg-red-50 border border-red-200 px-4 py-2.5 rounded-xl">{error}</p>
            )}

            <button type="submit" disabled={saving}
              className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white font-semibold py-3 rounded-xl transition-colors text-sm">
              {saving ? 'Saving...' : 'Save Resume'}
            </button>
          </form>

          <div className="sticky top-4 self-start">
            <div className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between bg-gray-50">
                <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Live Preview</span>
                <span className="text-xs text-gray-400">Updates as you type</span>
              </div>
              <div className="p-3 bg-gray-200 overflow-auto max-h-[85vh]">
                <ResumePreview form={form} photoPreview={photoPreview} />
              </div>
            </div>
          </div>
        </div>
      </div>
    </MainLayout>
  );
}

function SectionTitle({ children }) {
  return (
    <div style={{
      fontSize: '8.5px', fontWeight: '700', color: '#1e3a5f',
      textTransform: 'uppercase', letterSpacing: '1px',
      borderBottom: '1.5px solid #1e3a5f', paddingBottom: '3px', marginBottom: '8px',
    }}>
      {children}
    </div>
  );
}

function ResumePreview({ form, photoPreview }) {
  const skills = (form.skills || '').split(',').map((s) => s.trim()).filter(Boolean);
  const languages = (form.languages || '').split(',').map((s) => s.trim()).filter(Boolean);
  const experienceLines = (form.experience || '').split('\n').filter(Boolean);
  const educationLines = (form.education || '').split('\n').filter(Boolean);

  return (
    <div style={{
      width: '100%', background: '#fff',
      fontFamily: "'Segoe UI', Arial, sans-serif",
      fontSize: '10.5px', lineHeight: '1.55', color: '#222',
      boxShadow: '0 2px 12px rgba(0,0,0,0.18)',
    }}>
      <div style={{
        background: '#1e3a5f', color: '#fff',
        padding: '18px 22px', display: 'flex', alignItems: 'center', gap: '14px',
      }}>
        {photoPreview ? (
          <img src={photoPreview} alt="" style={{
            width: 58, height: 58, borderRadius: '50%', objectFit: 'cover',
            border: '2px solid rgba(255,255,255,0.45)', flexShrink: 0,
          }} />
        ) : (
          <div style={{
            width: 58, height: 58, borderRadius: '50%', background: '#2d5986',
            display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
          }}>
            <svg style={{ width: 28, height: 28 }} fill="none" stroke="#7ab2d6" strokeWidth="1.5" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
            </svg>
          </div>
        )}
        <div style={{ overflow: 'hidden', flex: 1 }}>
          <div style={{ fontSize: '16px', fontWeight: '700', letterSpacing: '0.3px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {form.full_name || 'Your Name'}
          </div>
          <div style={{ fontSize: '9.5px', color: '#a8c4e0', marginTop: '3px' }}>
            {[form.phone, form.email, form.location].filter(Boolean).join('  \u00b7  ') || 'Phone \u00b7 Email \u00b7 Location'}
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', minHeight: '420px' }}>
        <div style={{
          width: '32%', background: '#f0f4f8', padding: '16px 12px',
          borderRight: '1px solid #e2e8f0', flexShrink: 0,
        }}>
          {skills.length > 0 && (
            <div style={{ marginBottom: '16px' }}>
              <SectionTitle>Skills</SectionTitle>
              {skills.map((s, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: '5px', marginBottom: '4px' }}>
                  <span style={{ width: '5px', height: '5px', borderRadius: '50%', background: '#3b82f6', display: 'inline-block', flexShrink: 0, marginTop: '4px' }} />
                  <span>{s}</span>
                </div>
              ))}
            </div>
          )}
          {languages.length > 0 && (
            <div>
              <SectionTitle>Languages</SectionTitle>
              {languages.map((l, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '5px', marginBottom: '4px' }}>
                  <span style={{ width: '5px', height: '5px', borderRadius: '50%', background: '#64748b', display: 'inline-block', flexShrink: 0 }} />
                  {l}
                </div>
              ))}
            </div>
          )}
          {!skills.length && !languages.length && (
            <div style={{ color: '#94a3b8', fontSize: '9px', fontStyle: 'italic' }}>Skills and languages will appear here...</div>
          )}
        </div>

        <div style={{ flex: 1, padding: '16px', overflow: 'hidden' }}>
          {form.profile_summary && (
            <div style={{ marginBottom: '14px' }}>
              <SectionTitle>Summary</SectionTitle>
              <p style={{ margin: 0, color: '#444', lineHeight: '1.6' }}>{form.profile_summary}</p>
            </div>
          )}
          {experienceLines.length > 0 && (
            <div style={{ marginBottom: '14px' }}>
              <SectionTitle>Experience</SectionTitle>
              {experienceLines.map((line, i) => {
                const isBullet = line.trim().startsWith('\u2022') || line.trim().startsWith('-');
                return (
                  <p key={i} style={{
                    margin: `0 0 ${isBullet ? '3px' : '6px'} ${isBullet ? '8px' : '0'}`,
                    color: isBullet ? '#555' : '#1a202c',
                    fontWeight: isBullet ? 'normal' : '600',
                    lineHeight: '1.5',
                  }}>{line}</p>
                );
              })}
            </div>
          )}
          {educationLines.length > 0 && (
            <div>
              <SectionTitle>Education</SectionTitle>
              {educationLines.map((line, i) => (
                <p key={i} style={{ margin: '0 0 4px 0', color: '#333', lineHeight: '1.5' }}>{line}</p>
              ))}
            </div>
          )}
          {!form.profile_summary && !experienceLines.length && !educationLines.length && (
            <div style={{ color: '#94a3b8', fontSize: '9px', fontStyle: 'italic', paddingTop: '8px' }}>
              Summary, experience, and education will appear here...
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
