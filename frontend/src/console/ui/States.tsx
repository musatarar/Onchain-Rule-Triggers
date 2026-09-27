import type { ReactNode } from 'react';
import { Icon } from './Icon.tsx';

/** Dim placeholder lines while a pane's data is on its way. */
export function Skeleton({ rows = 5, lines = 2, label }: { rows?: number; lines?: number; label: string }) {
  return (
    <div className="skel" role="status" aria-label={label}>
      {Array.from({ length: rows }, (_, row) => (
        <div className="skel-row" key={row}>
          {Array.from({ length: lines }, (_, line) => (
            <span key={line} className="skel-line" style={{ width: `${88 - ((row * 17 + line * 29) % 45)}%` }} />
          ))}
        </div>
      ))}
    </div>
  );
}

/** A pane whose source failed: what went wrong, and a way to ask again. */
export function ErrorState({ what, error, onRetry }: { what: string; error: string; onRetry: () => void }) {
  return (
    <div className="empty-state err" role="alert">
      <b>{what}</b>
      <p className="note abn">
        <Icon name="warn" />
        <span>{error}</span>
      </p>
      <button type="button" className="btn sm" onClick={onRetry}>
        <Icon name="retry" />
        Retry
      </button>
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty-state">
      <b>{title}</b>
      {children}
    </div>
  );
}
