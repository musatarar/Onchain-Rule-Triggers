import { useId } from 'react';
import type { ReactNode } from 'react';

/**
 * An entry point that exists but is switched off: dimmed, not a link, and
 * focusable so keyboard users get the same "not available yet" tooltip that
 * hover shows. `side` keeps the tooltip inside whatever clips it — the tab
 * strip scrolls sideways on phones, so its tooltip sits to the right.
 */
export function Unavailable({
  className,
  side = 'above',
  children,
}: {
  className: string;
  side?: 'above' | 'right';
  children: ReactNode;
}) {
  const tipId = useId();
  return (
    <span className={`${className} off`} aria-disabled="true" tabIndex={0} aria-describedby={tipId}>
      {children}
      <span className={`soon soon-${side}`} role="tooltip" id={tipId}>
        NOT AVAILABLE YET
      </span>
    </span>
  );
}
