import { useEffect, useState } from 'react';
import {
  SEARCH_PHASE_LABEL,
  formatNursesContacted,
  formatSearchDistanceKm,
  isPastShiftStart,
} from '../../utils/staffingStatusCopy';
import { parseShiftDateTime } from '../../utils/shiftDateTime';

function elapsedSince(ts) {
  if (!ts) return null;
  const sec = Math.floor((Date.now() - ts) / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ${sec % 60}s`;
  return `${Math.floor(min / 60)}h ${min % 60}m`;
}

function countdownToShiftStart(iso) {
  const start = parseShiftDateTime(iso);
  if (!start) return null;
  const ms = start.getTime() - Date.now();
  if (ms <= 0) return 'Shift start time passed';
  const sec = Math.floor(ms / 1000);
  if (sec < 3600) return `Starts in ${Math.floor(sec / 60)} min`;
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return `Starts in ${h}h ${m}m`;
}

/**
 * Inline live staff-search activity for a single shift card.
 */
export default function ShiftDispatchLive({ shift, live, dispatchStartTime }) {
  const [, setTick] = useState(0);
  const pastStart = isPastShiftStart(shift?.shift_start);
  const searchActive = shift?.search_active !== false && !shift?.search_closed;
  const confirmed = shift?.confirmed_count ?? shift?.applicants?.filter((a) => a.status === 'confirmed')?.length ?? 0;
  const isActive =
    !pastStart &&
    searchActive &&
    (shift?.status === 'dispatching' ||
      shift?.status === 'open' ||
      live?.type === 'dispatch_started' ||
      live?.type === 'dispatch_wave_update' ||
      live?.type === 'nurse_accepted' ||
      (confirmed > 0 && searchActive));

  useEffect(() => {
    if (!isActive) return undefined;
    const id = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [isActive]);

  if (pastStart || (!isActive && live?.type !== 'dispatch_error')) return null;

  const phaseLabel =
    live?.message ||
    (live?.status && SEARCH_PHASE_LABEL[live.status]) ||
    (confirmed > 0 ? SEARCH_PHASE_LABEL.receiving : 'Finding nearby nurses…');

  const elapsed = elapsedSince(dispatchStartTime);
  const untilStart = countdownToShiftStart(shift?.shift_start);
  const nurseInfo = formatNursesContacted(live?.nurses_notified);
  const areaInfo = formatSearchDistanceKm(live?.radius_km);

  return (
    <div className="mt-2 rounded-xl border border-indigo-100 bg-gradient-to-r from-indigo-50 to-blue-50 px-3 py-2.5">
      <div className="flex items-center gap-2">
        <span className="relative flex h-2.5 w-2.5 shrink-0">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-indigo-400 opacity-60" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-indigo-600" />
        </span>
        <span className="text-xs font-semibold text-indigo-800 flex-1 leading-snug">{phaseLabel}</span>
        {elapsed && (
          <span className="text-xs font-medium text-indigo-600 tabular-nums shrink-0">{elapsed}</span>
        )}
      </div>
      <div className="flex items-center gap-2 mt-1.5 pl-4">
        <span className="w-3 h-3 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin shrink-0" />
        <span className="text-xs text-indigo-700">
          {confirmed > 0 ? 'Staff search active' : 'Finding nearby staff'}
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5 mt-1.5 pl-4">
        {nurseInfo && (
          <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-white/70 text-gray-600">
            {nurseInfo}
          </span>
        )}
        {areaInfo && (
          <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-white/70 text-gray-600">
            {areaInfo}
          </span>
        )}
        {untilStart && (
          <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-white/70 text-gray-600">
            {untilStart}
          </span>
        )}
      </div>
    </div>
  );
}
