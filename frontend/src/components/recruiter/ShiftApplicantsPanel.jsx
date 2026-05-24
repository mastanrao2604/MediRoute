import AssignedNurseCard from './AssignedNurseCard';

/**
 * Confirmed + in-progress applicants for an active staffing shift.
 */
export default function ShiftApplicantsPanel({
  shift,
  onViewProfile,
  onConfirmStaff,
  onMarkNoShow,
  confirmingNurseId = null,
  shiftBusy = false,
}) {
  const applicants = shift?.applicants || [];
  const confirmed = shift?.confirmed_count ?? applicants.length;
  const required = shift?.nurses_required ?? 1;
  const pending = shift?.pending_responses ?? 0;
  const searchActive = shift?.search_active !== false && !shift?.search_closed;

  if (!applicants.length && !pending && !searchActive) return null;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-semibold text-indigo-900 bg-indigo-50 px-2 py-1 rounded-lg">
          {confirmed} of {required} staff assigned
          {shift?.applied_count > 0 && ` · ${shift.applied_count} to review`}
        </span>
        {searchActive && (
          <span className="text-indigo-700 font-medium">Staff search active</span>
        )}
        {!searchActive && shift?.search_closed && (
          <span className="text-green-800 font-medium">Applications closed</span>
        )}
        {pending > 0 && (
          <span className="text-amber-800">{pending} waiting for response</span>
        )}
        {searchActive && confirmed > 0 && confirmed < required && (
          <span className="text-indigo-700">Searching for more staff</span>
        )}
      </div>
      {applicants.map((nurse) => (
        <AssignedNurseCard
          key={nurse.user_id || nurse.assignment_id}
          nurse={nurse}
          compact
          onViewProfile={onViewProfile}
          onConfirmStaff={onConfirmStaff}
          onMarkNoShow={onMarkNoShow}
          canConfirm={searchActive && nurse.status === 'applied'}
          canMarkNoShow={Boolean(nurse.can_mark_no_show) && Boolean(onMarkNoShow)}
          noShowOverdue={Boolean(nurse.no_show_overdue)}
          markBusy={shiftBusy}
          confirmBusy={confirmingNurseId === nurse.user_id}
        />
      ))}
    </div>
  );
}
