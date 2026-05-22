import { useEffect, useState } from 'react';
import { formatAreaDisplaySync, resolveAreaLabel } from '../utils/areaLabel';

/**
 * Resolves a stable locality label for shift/profile rows (async pincode lookup with cache).
 */
export function useAreaLabel(source) {
  const [label, setLabel] = useState(() => formatAreaDisplaySync(source || {}));

  useEffect(() => {
    let cancelled = false;
    const sync = formatAreaDisplaySync(source || {});
    setLabel(sync);
    (async () => {
      const resolved = await resolveAreaLabel(source || {});
      if (!cancelled && resolved) setLabel(resolved);
    })();
    return () => {
      cancelled = true;
    };
  }, [
    source?.locality,
    source?.pincode,
    source?.lat,
    source?.lng,
    source?.cityId,
  ]);

  return label;
}
