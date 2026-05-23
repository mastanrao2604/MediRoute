import { formatShiftDateTime } from '../utils/shiftDateTime';
import {
  formatRoleLabel,
  shiftStatusLabel,
  urgencyLabel,
} from '../utils/staffingStatusCopy';
import { useAreaLabel } from '../hooks/useAreaLabel';
import { shiftAreaSource } from '../utils/areaLabel';

const URGENCY_STYLES = {
  emergency: 'bg-red-100 text-red-800',
  urgent: 'bg-orange-100 text-orange-800',
  standard: 'bg-slate-100 text-slate-700',
  planned: 'bg-blue-100 text-blue-800',
};

const STATUS_STYLES = {
  open: 'bg-emerald-50 text-emerald-800',
  dispatching: 'bg-amber-50 text-amber-900',
};

/**
 * Instant shift row for the Jobs feed (open / actively finding staff).
 */
export default function ShiftListingCard({
  shift,
  onSelect,
  hasInvite,
  nearbyMatch = true,
  acceptEligible = true,
  confirmed = false,
}) {
  const areaLabel = useAreaLabel(shiftAreaSource(shift));
  const startLabel = formatShiftDateTime(shift.shift_start, {
    dateStyle: 'medium',
    timeStyle: 'short',
  });

  const urgClass = URGENCY_STYLES[shift.urgency] || 'bg-gray-100 text-gray-700';
  const stClass = STATUS_STYLES[shift.status] || 'bg-gray-50 text-gray-700';

  return (
    <button
      type="button"
      onClick={() => onSelect?.(shift)}
      className="w-full text-left bg-white rounded-2xl shadow-sm border border-amber-100 p-5 flex flex-col gap-3 hover:shadow-md hover:border-amber-200 transition-shadow ring-1 ring-amber-500/10 active:scale-[0.99] touch-manipulation"
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-amber-700">Instant shift</p>
          <h3 className="font-semibold text-gray-900 text-base leading-snug mt-1">
            {shift.hospital_name || 'Hospital'}
          </h3>
          {shift.specialty && (
            <p className="text-sm text-gray-600 mt-0.5">{shift.specialty}</p>
          )}
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <span className={`text-xs px-2 py-1 rounded-full font-medium ${urgClass}`}>
            {urgencyLabel(shift.urgency)}
          </span>
          <span className={`text-xs px-2 py-1 rounded-full font-medium ${stClass}`}>
            {shiftStatusLabel(shift.status)}
          </span>
          {confirmed && (
            <span className="text-xs px-2 py-1 rounded-full font-semibold bg-green-100 text-green-800">
              Shift confirmed
            </span>
          )}
          {!confirmed && hasInvite && acceptEligible && (
            <span className="text-xs px-2 py-1 rounded-full font-semibold bg-green-100 text-green-800">
              Ready to accept
            </span>
          )}
          {!confirmed && hasInvite && !acceptEligible && (
            <span className="text-xs px-2 py-1 rounded-full font-medium bg-slate-100 text-slate-700">
              View only
            </span>
          )}
          {!hasInvite && nearbyMatch && (
            <span className="text-xs px-2 py-1 rounded-full font-medium bg-amber-50 text-amber-900">
              Near you
            </span>
          )}
        </div>
      </div>

      <div className="flex flex-wrap gap-3 text-sm text-gray-600">
        {areaLabel && (
          <span className="font-medium text-indigo-800">{areaLabel}</span>
        )}
        {shift.role_required && (
          <span className="font-medium text-gray-800">{formatRoleLabel(shift.role_required)}</span>
        )}
        <span>{startLabel}</span>
        {shift.pay_rate && (
          <span className="text-green-700 font-medium">{shift.pay_rate}</span>
        )}
      </div>

      {shift.notes && (
        <p className="text-sm text-gray-600 line-clamp-2">{shift.notes}</p>
      )}

      <p className="text-xs text-indigo-600 font-medium">View details →</p>
    </button>
  );
}
