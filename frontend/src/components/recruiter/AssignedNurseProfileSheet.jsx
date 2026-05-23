import { createPortal } from 'react-dom';
import { useLockBodyScroll } from '../../hooks/useLockBodyScroll';
import { formatRoleLabel } from '../../utils/staffingStatusCopy';

export default function AssignedNurseProfileSheet({ nurse, shiftLabel, onClose }) {
  useLockBodyScroll(Boolean(nurse));

  if (!nurse || typeof document === 'undefined') return null;

  return createPortal(
    <div className="fixed inset-0 z-[110] flex flex-col justify-end" role="dialog" aria-modal="true">
      <button type="button" className="absolute inset-0 bg-black/40" aria-label="Close" onClick={onClose} />
      <div
        className="relative z-[111] bg-white rounded-t-2xl shadow-2xl max-h-[min(85dvh,calc(100dvh-env(safe-area-inset-top,0px)-3.5rem))] flex flex-col"
        style={{ paddingBottom: 'var(--mobile-bottom-safe)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="shrink-0 flex items-center justify-between border-b border-gray-100 px-4 py-3">
          <h2 className="text-lg font-bold text-gray-900">Staff profile</h2>
          <button
            type="button"
            onClick={onClose}
            className="min-w-[44px] min-h-[44px] flex items-center justify-center text-gray-500 rounded-xl hover:bg-gray-100"
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto px-4 py-4">
          <div className="rounded-2xl bg-indigo-600 text-white p-5 mb-4">
            <p className="text-indigo-200 text-xs uppercase tracking-wide font-medium">Confirmed for</p>
            <p className="text-sm mt-1 text-indigo-100">{shiftLabel || 'This shift'}</p>
            <p className="text-2xl font-bold mt-3">{nurse.name}</p>
            {nurse.phone && (
              <>
                <p className="text-indigo-100 font-mono text-lg mt-2">{nurse.phone}</p>
                <a
                  href={`tel:${nurse.phone}`}
                  className="mt-4 flex items-center justify-center w-full bg-white text-indigo-700 font-bold py-3 rounded-xl"
                >
                  Call nurse
                </a>
              </>
            )}
          </div>
          <dl className="space-y-3 text-sm">
            {nurse.role && (
              <div>
                <dt className="text-xs text-gray-400 uppercase">Role</dt>
                <dd className="font-medium text-gray-900">{formatRoleLabel(nurse.role)}</dd>
              </div>
            )}
            {nurse.experience_years != null && (
              <div>
                <dt className="text-xs text-gray-400 uppercase">Experience</dt>
                <dd className="font-medium text-gray-900">{nurse.experience_years} years</dd>
              </div>
            )}
            {nurse.rating != null && (
              <div>
                <dt className="text-xs text-gray-400 uppercase">Reliability</dt>
                <dd className="font-medium text-gray-900">{Number(nurse.rating).toFixed(0)}%</dd>
              </div>
            )}
            {nurse.completed_shifts != null && nurse.completed_shifts > 0 && (
              <div>
                <dt className="text-xs text-gray-400 uppercase">Completed shifts</dt>
                <dd className="font-medium text-gray-900">{nurse.completed_shifts}</dd>
              </div>
            )}
            {nurse.service_locality && (
              <div>
                <dt className="text-xs text-gray-400 uppercase">Area</dt>
                <dd className="font-medium text-gray-900">{nurse.service_locality}</dd>
              </div>
            )}
            {nurse.skills && (
              <div>
                <dt className="text-xs text-gray-400 uppercase">Skills</dt>
                <dd className="text-gray-800 whitespace-pre-line">{nurse.skills}</dd>
              </div>
            )}
            {nurse.education && (
              <div>
                <dt className="text-xs text-gray-400 uppercase">Education</dt>
                <dd className="text-gray-800 whitespace-pre-line">{nurse.education}</dd>
              </div>
            )}
          </dl>
        </div>
      </div>
    </div>,
    document.body,
  );
}
