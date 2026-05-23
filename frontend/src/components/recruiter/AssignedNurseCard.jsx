/**
 * Compact confirmed staff card for recruiter shift list / detail.
 */
export default function AssignedNurseCard({
  nurse,
  onViewProfile,
  onConfirmStaff,
  confirmBusy = false,
  canConfirm = false,
  compact = false,
}) {
  if (!nurse?.user_id && !nurse?.name) return null;

  const rating =
    nurse.rating != null ? `${Number(nurse.rating).toFixed(0)}% reliable` : null;
  const exp =
    nurse.experience_years != null ? `${nurse.experience_years} yrs experience` : null;

  return (
    <div
      className={`rounded-xl border ${
        nurse.status === 'waiting'
          ? 'border-amber-200 bg-amber-50/80'
          : 'border-green-200 bg-green-50/80'
      } ${compact ? 'px-3 py-2.5' : 'px-3 py-3'}`}
    >
      <div className="flex items-start gap-3">
        <div
          className="shrink-0 w-10 h-10 rounded-full bg-green-600 text-white flex items-center justify-center text-sm font-bold"
          aria-hidden
        >
          {(nurse.name || '?').charAt(0).toUpperCase()}
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-xs font-semibold text-green-800 uppercase tracking-wide">
            {nurse.status === 'confirmed'
              ? 'Confirmed nurse'
              : nurse.status === 'waiting'
                ? 'Waiting for response'
                : 'Interested nurse'}
          </p>
          <p className="text-sm font-bold text-gray-900 truncate mt-0.5">{nurse.name}</p>
          <div className="flex flex-wrap gap-x-2 gap-y-0.5 mt-1 text-xs text-gray-600">
            {nurse.role && (
              <span className="capitalize">{String(nurse.role).replace(/_/g, ' ')}</span>
            )}
            {rating && <span>{rating}</span>}
            {exp && <span>{exp}</span>}
            {nurse.service_locality && <span>{nurse.service_locality}</span>}
          </div>
          {nurse.phone && (
            <a
              href={`tel:${nurse.phone}`}
              onClick={(e) => e.stopPropagation()}
              className="inline-flex items-center gap-1 mt-2 text-xs font-semibold text-green-800 hover:text-green-900"
            >
              Contact nurse · {nurse.phone}
            </a>
          )}
        </div>
      </div>
      <div className="mt-2 flex flex-col gap-2">
        {canConfirm && onConfirmStaff && (
          <button
            type="button"
            disabled={confirmBusy}
            onClick={(e) => {
              e.stopPropagation();
              onConfirmStaff(nurse);
            }}
            className="w-full text-sm font-bold text-white py-2.5 rounded-xl bg-green-600 hover:bg-green-700 disabled:opacity-50"
          >
            {confirmBusy ? 'Confirming…' : 'Confirm nurse'}
          </button>
        )}
        {onViewProfile && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onViewProfile(nurse);
            }}
            className="w-full text-xs font-semibold text-indigo-700 py-2 rounded-lg bg-white/80 border border-green-100 hover:bg-white"
          >
            View profile
          </button>
        )}
      </div>
    </div>
  );
}
