import { useEffect, useState, type CSSProperties, type ReactElement } from 'react';

/**
 * The tube powering on (DESIGN.md → Power-on boot). Plays once per page load:
 * a dot at the centre of the glass stretches into a hot horizontal line, and the
 * picture opens up and down from that line to fill the tube.
 *
 * Shells can swap mid-boot (RequireAuth's session check hands over to the
 * console), so the boot is timed from the first shell that mounts and later
 * shells join it part-way with a negative delay instead of starting again.
 */
export const BOOT_MS = 1150;

let firstMount: number | null = null;

function reducedMotion(): boolean {
  return typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
}

export function useBoot(): { className: string; style: CSSProperties | undefined; overlay: ReactElement | null } {
  const [elapsed] = useState(() => {
    const now = performance.now();
    if (firstMount === null) firstMount = reducedMotion() ? now - BOOT_MS : now;
    return now - firstMount;
  });
  const [on, setOn] = useState(elapsed < BOOT_MS);

  useEffect(() => {
    if (!on) return;
    const timer = window.setTimeout(() => setOn(false), BOOT_MS - elapsed);
    return () => window.clearTimeout(timer);
  }, [on, elapsed]);

  if (!on) return { className: 'scr-in', style: undefined, overlay: null };
  return {
    className: 'scr-in booting',
    style: { '--boot-at': `${-Math.round(elapsed)}ms` } as CSSProperties,
    overlay: (
      <span className="boot" aria-hidden="true">
        <i />
      </span>
    ),
  };
}
