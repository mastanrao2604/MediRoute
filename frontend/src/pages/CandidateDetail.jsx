import { useState, useEffect } from 'react';
import { useParams, useNavigate, useLocation } from 'react-router-dom';
import api from '../api/axios';
import MainLayout from '../layouts/MainLayout';
import Spinner from '../components/Spinner';
import RecruiterSubpageHeader from '../components/recruiter/RecruiterSubpageHeader';
import { recruiterGoBack } from '../utils/recruiterNav';

export default function CandidateDetail() {
  const { applicationId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [candidate, setCandidate] = useState(null);
  const [fetching, setFetching] = useState(true);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  const [resumeLoading, setResumeLoading] = useState(false);
  const [resumeError, setResumeError] = useState('');

  useEffect(() => {
    api.get(`/recruiter/applications/${applicationId}`)
      .then((res) => setCandidate(res.data))
      .catch(() => setError('Failed to load candidate details.'))
      .finally(() => setFetching(false));
  }, [applicationId]);

  function copyPhone() {
    navigator.clipboard.writeText(candidate.phone).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  async function downloadResume() {
    setResumeLoading(true);
    setResumeError('');
    try {
      const res = await api.get(`/resume/download/${candidate.candidate_user_id}`, { responseType: 'blob' });
      const url = URL.createObjectURL(new Blob([res.data], { type: 'application/pdf' }));
      window.open(url, '_blank');
    } catch (err) {
      const msg = err.response?.status === 403
        ? 'Access denied.'
        : err.response?.status === 404
        ? 'No resume uploaded by this candidate.'
        : 'Download failed.';
      setResumeError(msg);
      setTimeout(() => setResumeError(''), 3000);
    } finally {
      setResumeLoading(false);
    }
  }

  if (fetching) {
    return (
      <MainLayout>
        <div className="flex justify-center py-20"><Spinner /></div>
      </MainLayout>
    );
  }

  if (error || !candidate) {
    return (
      <MainLayout>
        <div className="max-w-lg mx-auto px-4 py-16 text-center">
          <p className="text-gray-500">{error || 'Candidate not found.'}</p>
          <button
            type="button"
            onClick={() => recruiterGoBack(navigate, location, '/recruiter/dashboard')}
            className="mt-4 min-h-11 px-4 text-indigo-600 text-sm font-medium rounded-xl hover:bg-indigo-50"
          >
            Go back
          </button>
        </div>
      </MainLayout>
    );
  }

  return (
    <MainLayout>
      <div className="max-w-lg mx-auto px-4 sm:px-6 lg:px-4">
        <RecruiterSubpageHeader
          title={candidate.candidate_name || candidate.phone || 'Candidate'}
          subtitle={[
            location.state?.jobTitle,
            candidate.status ? `Status: ${candidate.status}` : null,
          ].filter(Boolean).join(' · ') || 'Application details'}
          onBack={() => recruiterGoBack(navigate, location, '/recruiter/dashboard')}
        />

        <div className="pt-4 pb-6">

        {/* Contact Card */}
        <div className="bg-indigo-600 rounded-2xl p-5 mb-4 text-white">
          <p className="text-indigo-200 text-xs font-medium uppercase tracking-wide mb-1">Candidate</p>
          <p className="text-2xl font-bold leading-snug">{candidate.candidate_name || '—'}</p>

          {/* Phone number — large, scannable */}
          <div className="flex items-center gap-2 mt-2">
            <span className="text-indigo-100 text-lg font-mono font-semibold tracking-wide">
              {candidate.phone}
            </span>
            <button
              onClick={copyPhone}
              title="Copy number"
              className="text-xs text-indigo-200 hover:text-white border border-indigo-400 hover:border-white px-2 py-0.5 rounded-lg transition-colors"
            >
              {copied ? 'Copied!' : 'Copy'}
            </button>
          </div>

          {/* Primary CTA — full width, impossible to miss */}
          <a
            href={`tel:${candidate.phone}`}
            className="mt-4 flex items-center justify-center gap-2 w-full bg-white text-indigo-700 font-bold px-4 py-3 rounded-xl text-base hover:bg-indigo-50 active:scale-95 transition-all"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 7V5z" />
            </svg>
            Call Now
          </a>
          <p className="text-center text-indigo-300 text-xs mt-2">Tap to open your dialler</p>
        </div>

        {/* Profile Details */}
        <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-6 flex flex-col gap-4">
          <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide">Profile</h2>
          <DetailRow label="Experience"  value={candidate.experience_years != null ? `${candidate.experience_years} years` : null} />
          <DetailRow label="Skills"      value={candidate.skills} />
          <DetailRow label="Education"   value={candidate.education} />
          <DetailRow label="Location"    value={candidate.location} />

          {(candidate.resume_skills || candidate.resume_experience) && (
            <>
              <hr className="border-gray-100" />
              <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide">From Resume</h2>
              <DetailRow label="Resume Skills"     value={candidate.resume_skills} />
              <DetailRow label="Resume Experience" value={candidate.resume_experience} />
            </>
          )}
        </div>

        <div className="mt-4 flex items-center justify-between text-xs text-gray-400">
          <span>Status: <strong className="text-gray-600 capitalize">{candidate.status}</strong></span>
          <span>Application #{candidate.application_id}</span>
        </div>

        {/* Resume download */}
        {candidate.has_resume && (
          <div className="mt-4">
            {resumeError && (
              <p className="text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2 mb-2">{resumeError}</p>
            )}
            <button
              onClick={downloadResume}
              disabled={resumeLoading}
              className="w-full flex items-center justify-center gap-2 bg-indigo-50 hover:bg-indigo-100 disabled:opacity-60 text-indigo-700 font-semibold py-3 rounded-xl transition-colors border border-indigo-200"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
              </svg>
              {resumeLoading ? 'Opening…' : 'View / Download Resume'}
            </button>
          </div>
        )}
        </div>
      </div>
    </MainLayout>
  );
}

function DetailRow({ label, value }) {
  if (!value) return null;
  return (
    <div>
      <p className="text-xs font-medium text-gray-400 uppercase tracking-wide mb-0.5">{label}</p>
      <p className="text-sm text-gray-800 whitespace-pre-line">{value}</p>
    </div>
  );
}
