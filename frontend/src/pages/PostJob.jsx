import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api/axios';
import MainLayout from '../layouts/MainLayout';
import { useAuth } from '../context/AuthContext';

const ROLE_OPTIONS = ['nurse', 'staff_nurse', 'icu_nurse', 'ot_nurse', 'emergency_nurse', 'home_care_nurse', 'doctor', 'lab_tech', 'pharmacist', 'driver', 'front_office'];
const JOB_TYPE_OPTIONS = ['india', 'abroad', 'both'];

export default function PostJob() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const isVerified = user?.is_verified === true;
  const [form, setForm] = useState({
    title: '',
    role_required: '',
    hospital_name: '',
    location: '',
    country: '',
    job_type: 'india',
    salary: '',
    description: '',
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  function handleChange(e) {
    setForm((f) => ({ ...f, [e.target.name]: e.target.value }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!form.title.trim()) { setError('Job title is required.'); return; }
    if (!form.hospital_name.trim()) { setError('Hospital / Organisation name is required.'); return; }
    if (!form.location.trim()) { setError('City / Location is required.'); return; }
    setError('');
    setLoading(true);
    try {
      const payload = {
        ...form,
        role_required: form.role_required || null,
        country: form.country.trim() || null,
        salary: form.salary.trim() || null,
        description: form.description.trim() || null,
      };
      await api.post('/recruiter/jobs', payload);
      navigate('/recruiter/dashboard');
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to post job.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <MainLayout>
      <div className="max-w-lg mx-auto px-4 py-6">
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-gray-900">Post a Job</h1>
          <p className="text-sm text-gray-500 mt-1">Fill in the details to attract the right candidates</p>
        </div>

        {!isVerified && (
          <div className="bg-amber-50 border border-amber-200 rounded-2xl p-4 mb-5 flex items-start gap-3">
            <span className="text-amber-500 text-lg">⚠️</span>
            <div>
              <p className="text-sm font-semibold text-amber-800">Verification required</p>
              <p className="text-xs text-amber-700 mt-0.5">
                Your account is pending verification. You cannot post jobs until our team approves your company profile.
              </p>
            </div>
          </div>
        )}

        <form onSubmit={handleSubmit} className="bg-white rounded-2xl border border-gray-100 shadow-sm p-6 flex flex-col gap-5">

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">
              Job Title <span className="text-red-500">*</span>
            </label>
            <input
              type="text" name="title" value={form.title} onChange={handleChange}
              placeholder="e.g. Staff Nurse – ICU"
              className="w-full border border-gray-300 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">Role Required <span className="text-red-500">*</span></label>
            <select name="role_required" value={form.role_required} onChange={handleChange}
              className="w-full border border-gray-300 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition bg-white"
            >
              <option value="">— Any Role —</option>
              {ROLE_OPTIONS.map((r) => (
                <option key={r} value={r}>{r.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase())}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">Hospital / Organisation <span className="text-red-500">*</span></label>
            <input
              type="text" name="hospital_name" value={form.hospital_name} onChange={handleChange}
              placeholder="e.g. Apollo Hospitals"
              className="w-full border border-gray-300 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition"
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">City / Location <span className="text-red-500">*</span></label>
              <input
                type="text" name="location" value={form.location} onChange={handleChange}
                placeholder="e.g. Chennai"
                className="w-full border border-gray-300 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">Country</label>
              <input
                type="text" name="country" value={form.country} onChange={handleChange}
                placeholder="e.g. India"
                className="w-full border border-gray-300 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">Job Type <span className="text-red-500">*</span></label>
              <select name="job_type" value={form.job_type} onChange={handleChange}
                className="w-full border border-gray-300 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition bg-white"
              >
                {JOB_TYPE_OPTIONS.map((t) => (
                  <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">Salary</label>
              <input
                type="text" name="salary" value={form.salary} onChange={handleChange}
                placeholder="e.g. ₹40,000/month"
                className="w-full border border-gray-300 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition"
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">Description</label>
            <textarea
              name="description" value={form.description} onChange={handleChange} rows={4}
              placeholder="Job responsibilities, requirements, etc."
              className="w-full border border-gray-300 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition resize-none"
            />
          </div>

          {error && <p className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded-lg">{error}</p>}

          <div className="flex gap-3">
            <button
              type="button" onClick={() => navigate('/recruiter/dashboard')}
              className="flex-1 border border-gray-300 hover:bg-gray-50 text-gray-700 font-semibold py-3 rounded-xl transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit" disabled={loading || !isVerified}
              title={!isVerified ? 'Verification required to post jobs' : ''}
              className="flex-1 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white font-semibold py-3 rounded-xl transition-colors"
            >
              {loading ? 'Posting…' : 'Post Job'}
            </button>
          </div>
        </form>
      </div>
    </MainLayout>
  );
}
