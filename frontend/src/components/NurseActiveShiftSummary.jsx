import { activeShiftMetaLine, activeShiftSummaryLine } from '../utils/nurseActiveShift';

/**
 * Compact active shift row inside "Available for Shifts" on the nurse dashboard.
 */
export default function NurseActiveShiftSummary({ shift, onOpen }) {
  if (!shift) return null;

  return (
    <button
      type="button"
      onClick={onOpen}
      className="mt-3 w-full text-left rounded-xl border border-indigo-200 bg-white/90 px-3 py-2.5 shadow-sm hover:border-indigo-300 hover:bg-indigo-50/50 active:scale-[0.99] transition-colors touch-manipulation"
    >
      <p className="text-[11px] font-bold uppercase tracking-wide text-indigo-700">
        Your current shift
      </p>
      <p className="text-sm font-semibold text-gray-900 truncate mt-0.5">
        {shift.hospital_name || 'Hospital'}
      </p>
      <p className="text-xs text-gray-600 mt-0.5 leading-snug line-clamp-2">
        {activeShiftSummaryLine(shift)}
      </p>
      <p className="text-xs text-gray-500 mt-0.5 truncate">
        {activeShiftMetaLine(shift)}
      </p>
      <p className="text-xs text-indigo-600 font-semibold mt-1.5">View full details →</p>
    </button>
  );
}
