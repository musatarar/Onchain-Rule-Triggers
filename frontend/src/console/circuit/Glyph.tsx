import type { ReactNode } from 'react';
import type { Glyph as GlyphName, RuleRef } from '../api/types.ts';

// 12 stroke-drawn symbols on a 16px grid, like the channel markers on a scope.
const PATHS: Record<GlyphName, ReactNode> = {
  triangle: <path d="M8 2 14.5 13.5H1.5Z" />,
  diamond: <path d="M8 1.5 14.5 8 8 14.5 1.5 8Z" />,
  target: (
    <>
      <circle cx="8" cy="8" r="5.5" />
      <path d="M8 1v4M8 11v4M1 8h4M11 8h4" />
    </>
  ),
  square: <rect x="2.5" y="2.5" width="11" height="11" />,
  star: <path d="M8 1.5 9.9 6l4.6.3-3.5 3 1.1 4.7L8 11.5 3.9 14 5 9.3l-3.5-3L6.1 6Z" />,
  bars: <path d="M2 4h12M2 8h12M2 12h12" />,
  chevron: <path d="M2 3.5l6 5 6-5M2 8.5l6 5 6-5" />,
  bolt: <path d="M9.5 1.5 3.5 9H8l-1.5 5.5 6-7.5H8Z" />,
  hexagon: <path d="M8 1.5 13.6 4.75v6.5L8 14.5 2.4 11.25v-6.5Z" />,
  circle: <circle cx="8" cy="8" r="6" />,
  xmark: <path d="M3 3 13 13M13 3 3 13" />,
  ring: (
    <>
      <circle cx="8" cy="8" r="6" />
      <circle cx="8" cy="8" r="2.5" />
    </>
  ),
};

export const glyphPaths = (name: GlyphName): ReactNode => PATHS[name];

export function Glyph({ name, className = '' }: { name: GlyphName; className?: string }) {
  return (
    <svg className={`gl ${className}`} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      {PATHS[name]}
    </svg>
  );
}

/** A circuit's identity: its glyph and tag, always together. */
export function Ident({ rule, big = false }: { rule: Pick<RuleRef, 'tag' | 'glyph'>; big?: boolean }) {
  return (
    <span className={`ident${big ? ' big' : ''}`}>
      <Glyph name={rule.glyph} />
      <b>{rule.tag}</b>
    </span>
  );
}
