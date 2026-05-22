import { useState, useEffect, useMemo } from 'react';
import { useParams, useNavigate, useLocation, Link } from 'react-router-dom';
import api from '../api/axios';
import MainLayout from '../layouts/MainLayout';
import Spinner from '../components/Spinner';
import RecruiterSubpageHeader from '../components/recruiter/RecruiterSubpageHeader';
import { recruiterGoBack } from '../utils/recruiterNav';

const STATUS_COLOR = {
  applied:     'bg-blue-50 text-blue-700',
  shortlisted: 'bg-green-50 text-green-700',
  rejected:    'bg-red-50 text-red-700',
};

export default function Applicants() {
  const { jobId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [applicants, setApplicants] = useState([]);
  const [fetching, setFetching] = useState(false);
  const [error, setError] = useState('');
  const [resumeError, setResumeError] = useState('');

  const jobTitle = location.state?.jobTitle;
  const jobHospital = location.state?.jobHospital;

  useEffect(() => {
    setFetching(true);
    api.get(`/recruiter/jobs/${jobId}/applicants`)
      .then((res) => setApplicants(res.data))
      .catch(() => setError('Failed to load applicants.'))
      .finally(() => setFetching(false));
  }, [jobId]);

  const subtitle = useMemo(() => {
    const parts = [];
    if (jobTitle) parts.push(jobTitle);
    else parts.push('Job post');
    if (jobHospital) parts.push(jobHospital);
    if (!fetching) {
      const n = applicants.length;
      parts.push(n === 1 ? '1 applicant' : `${n} applicants`);
    }
    return parts.join(' · ');
  }, [jobTitle, jobHospital, applicants.length, fetching]);

  const applicantsReturnState = useMemo(
    () => ({
      returnTo: '/recruiter/dashboard',
      jobTitle,
      jobHospital,
    }),
    [jobTitle, jobHospital],
  );

  async function handleDownloadResume(e, userId) {
    e.preventDefault();
    e.stopPropagation();
    setResumeError('');
    try {
      const res = await api.get(`/resume/download/${userId}`, { responseType: 'blob' });
      const url = URL.createObjectURL(new Blob([res.data], { type: 'application/pdf' }));
      window.open(url, '_blank');
    } catch (err) {
      const msg = err.response?.status === 403
        ? 'Access denied.'
        : err.response?.status === 404
        ? 'No resume uploaded yet.'
        : 'Download failed.';
      setResumeError(msg);
      setTimeout(() => setResumeError(''), 3000);
    }
  }

  return (
    <MainLayout>
      <div className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-4">
        <RecruiterSubpageHeader
          title="Applicants"
          subtitle={subtitle}
          onBack={() => recruiterGoBack(navigate, location)}
          rightSlot={
            !fetching && applicants.length > 0 ? (
              <span className="text-xs font-semibold text-indigo-700 bg-indigo-50 px-2.5 py-1 rounded-full tabular-nums">
                {applicants.length}
              </span>
            ) : null
          }
        />

        <div className="pt-4 pb-6">
          {error && <p className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded-lg mb-4">{error}</p>}
          {resumeError && <p className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded-lg mb-4">{resumeError}</p>}

          {fetching ? (
            <div className="flex justify-center py-20"><Spinner /></div>
          ) : applicants.length === 0 ? (
            <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-10 text-center">
              <p className="text-gray-500">No applicants yet for this job.</p>
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              {applicants.map((a) => (
                <Link
                  key={a.application_id}
                  to={`/recruiter/applications/${a.application_id}`}
                  state={{
                    returnTo: `/recruiter/jobs/${jobId}/applicants`,
                    returnState: applicantsReturnState,
                    jobTitle,
                  }}
                  className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5 hover:border-indigo-200 transition-colors block active:scale-[0.99]"
                >
                  <div className="flex items-start justify-between">
                    <div className="min-w-0 flex-1">
                      <p className="font-semibold text-gray-900">{a.candidate_name || a.phone || 'Unknown'}</p>
                      <p className="text-sm text-gray-500 mt-0.5">
                        {a.experience != null ? `${a.experience} yr exp` : 'Exp: —'} · {a.location || 'Location: —'}
                      </p>
                      {a.skills && (
                        <p className="text-xs text-gray-400 mt-1 truncate max-w-sm">{a.skills}</p>
                      )}
                    </div>
                    <div className="flex flex-col items-end gap-2 ml-3 shrink-0">
                      <span className={`text-xs px-2 py-1 rounded-lg font-medium ${STATUS_COLOR[a.status] || 'bg-gray-50 text-gray-700'}`}>
                        {a.status}
                      </span>
                      {a.has_resume && (
                        <button
                          onClick={(e) => handleDownloadResume(e, a.candidate_user_id)}
                          className="flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-800 border border-indigo-200 hover:border-indigo-400 px-2 py-1 rounded-lg transition-colors"
                          title="Download resume"
                        >
                          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                              d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                          </svg>
                          Resume
                        </button>
                      )}
                      <span className="text-xs text-indigo-600 font-medium">View →</span>
                    </div>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>
      </div>
    </MainLayout>
  );
}
