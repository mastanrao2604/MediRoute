import { useNavigate } from 'react-router-dom';

export default function JobCard({ job, showApply = false, onApply, applyLoading = false }) {
  const navigate = useNavigate();

  const jobTypeBadge = {
    india: { label: 'India', color: 'bg-green-100 text-green-700' },
    abroad: { label: 'Abroad', color: 'bg-blue-100 text-blue-700' },
    both: { label: 'India & Abroad', color: 'bg-amber-100 text-amber-700' },
  };

  const badge = jobTypeBadge[job.job_type] || { label: job.job_type, color: 'bg-gray-100 text-gray-600' };

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-5 flex flex-col gap-3 hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="font-semibold text-gray-900 text-base leading-snug">{job.title}</h3>
          {job.hospital_name && (
            <p className="text-sm text-indigo-600 mt-0.5">{job.hospital_name}</p>
          )}
        </div>
        <span className={`text-xs px-2 py-1 rounded-full font-medium shrink-0 ${badge.color}`}>
          {badge.label}
        </span>
      </div>

      <div className="flex flex-wrap gap-3 text-sm text-gray-500">
        {job.location && (
          <span className="flex items-center gap-1">
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
            {job.location}
          </span>
        )}
        {job.salary && (
          <span className="flex items-center gap-1 text-green-600 font-medium">
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            {job.salary}
          </span>
        )}
        {job.role_required && (
          <span className="flex items-center gap-1">
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
            </svg>
            {job.role_required}
          </span>
        )}
      </div>

      {job.description && (
        <p className="text-sm text-gray-600 line-clamp-2">{job.description}</p>
      )}

      <div className="flex gap-2 mt-1">
        <button
          onClick={() => navigate(`/jobs/${job.id}`)}
          className="flex-1 text-sm text-indigo-600 border border-indigo-200 hover:bg-indigo-50 px-4 py-3 rounded-xl font-medium transition-colors min-h-[44px]"
        >
          View Details
        </button>
        {showApply && (
          <button
            onClick={() => !applyLoading && onApply(job.id)}
            disabled={applyLoading}
            className="flex-1 text-sm bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white px-4 py-3 rounded-xl font-medium transition-colors min-h-[44px]"
          >
            {applyLoading ? 'Applying…' : 'Apply'}
          </button>
        )}
      </div>
    </div>
  );
}
