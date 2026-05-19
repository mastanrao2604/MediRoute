/**
 * Instant shift row for the Jobs feed (open / dispatching shifts).
 * Accept flow stays dispatch/WebSocket-driven — this card is browse-only context.
 */
export default function ShiftListingCard({ shift }) {
  const start = shift.shift_start ? new Date(shift.shift_start) : null;
  const startLabel = start && !Number.isNaN(start.getTime())
    ? start.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
    : '—';

  const urgencyStyles = {
    emergency: 'bg-red-100 text-red-800',
    urgent: 'bg-orange-100 text-orange-800',
    standard: 'bg-slate-100 text-slate-700',
    planned: 'bg-blue-100 text-blue-800',
  };
  const urgClass = urgencyStyles[shift.urgency] || 'bg-gray-100 text-gray-700';

  const statusStyles = {
    open: 'bg-emerald-50 text-emerald-800',
    dispatching: 'bg-amber-50 text-amber-900',
  };
  const stClass = statusStyles[shift.status] || 'bg-gray-50 text-gray-700';

  const roleLabel = shift.role_required
    ? String(shift.role_required).replace(/_/g, ' ')
    : '';

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-amber-100 p-5 flex flex-col gap-3 hover:shadow-md transition-shadow ring-1 ring-amber-500/10">
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
            {shift.urgency || 'standard'}
          </span>
          <span className={`text-xs px-2 py-1 rounded-full font-medium ${stClass}`}>
            {shift.status || 'open'}
          </span>
        </div>
      </div>

      <div className="flex flex-wrap gap-3 text-sm text-gray-600">
        {roleLabel && (
          <span className="font-medium text-gray-800 capitalize">{roleLabel}</span>
        )}
        <span>{startLabel}</span>
        {shift.pay_rate && (
          <span className="text-green-700 font-medium">{shift.pay_rate}</span>
        )}
        {shift.city_id && (
          <span className="text-gray-500">{shift.city_id}</span>
        )}
      </div>

      {shift.notes && (
        <p className="text-sm text-gray-600 line-clamp-3">{shift.notes}</p>
      )}

      <p className="text-xs text-gray-500 mt-1">
        Turn on availability to receive offers for shifts like this. You&apos;ll confirm via in-app dispatch when matched.
      </p>
    </div>
  );
}
