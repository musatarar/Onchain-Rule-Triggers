import { useEffect } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { pageTitle } from '../console/product.ts';
import { Mark } from '../console/ui/Mark.tsx';
import { NoSignal } from './NoSignal.tsx';
import type { Signal } from './NoSignal.tsx';
import '../console/console.css';
import './auth.css';

export type AuthTab = 'signin' | 'register' | 'link';

const TABS: { id: AuthTab; to: string; label: string; short: string }[] = [
  { id: 'signin', to: '/signin', label: 'SIGN IN', short: 'SIGN IN' },
  { id: 'register', to: '/register', label: 'NEW OPERATOR', short: 'NEW' },
  { id: 'link', to: '/signin?via=link', label: 'EMAIL LINK', short: 'LINK' },
];

const STATUS: Record<Signal, string> = {
  none: 'NONE',
  tuning: 'TUNING',
  fault: 'NONE',
  locking: 'LOCKING',
  locked: 'LOCKED',
};

interface Props {
  /** Page name for the tab title; the product suffix is added here. */
  title: string;
  /** The highlighted tab, or null to hide the tab strip (session check, consume). */
  tab: AuthTab | null;
  signal: Signal;
  /** Right-hand status-line text, e.g. "AWAITING OPERATOR". */
  status: string;
  children?: ReactNode;
}

/**
 * The signed-out monitor: the console's own bezel, tube and header, off-air.
 * Static fills the tube until an operator is recognised; the panel in the
 * middle is the only thing on the glass that isn't noise.
 */
export function TuningShell({ title, tab, signal, status, children }: Props) {
  useEffect(() => {
    document.title = pageTitle(title);
  }, [title]);

  const caption = signal === 'locking' || signal === 'locked' ? 'SIGNAL LOCKED' : signal === 'tuning' ? 'TUNING' : 'NO SIGNAL';

  return (
    <div className="phosphor">
      <div className="dev">
        <div className="screen">
          <div className="scr-in">
            <header className="sh">
              <div className="logo">
                <Mark />
                PHOSPHOR
              </div>
            </header>
            <nav className="stabs" aria-label="Ways to sign in">
              {tab &&
                TABS.map((t) => (
                  <Link key={t.id} to={t.to} className={t.id === tab ? 'tab active' : 'tab'} aria-current={t.id === tab ? 'page' : undefined}>
                    <span className="t-long">{t.label}</span>
                    <span className="t-short" aria-hidden="true">
                      {t.short}
                    </span>
                  </Link>
                ))}
            </nav>
            <main className={`main tune sig-${signal}`}>
              <NoSignal signal={signal} />
              <p className="tune-cap" aria-hidden="true">
                {caption}
              </p>
              {children}
            </main>
            <footer className="sl">
              <span>
                SIGNAL <b>{STATUS[signal]}</b>
              </span>
              <span>{status}</span>
              <span className="blink" aria-hidden="true">
                █
              </span>
            </footer>
          </div>
        </div>
      </div>
    </div>
  );
}

/** The one framed panel on the glass. `heading` is VT323; the rest is the form. */
export function TunePanel({ heading, lede, children, labelledBy = 'tune-h' }: { heading: ReactNode; lede?: ReactNode; children?: ReactNode; labelledBy?: string }) {
  return (
    <section className="tp" aria-labelledby={labelledBy}>
      <h1 className="tp-h" id={labelledBy}>
        {heading}
      </h1>
      {lede && <p className="tp-lede">{lede}</p>}
      {children}
    </section>
  );
}

/** A banner in terminal voice: a heading line, then the server's own sentence. */
export function TuneAlert({ heading, children }: { heading: string; children: ReactNode }) {
  return (
    <div className="tp-alert" role="alert">
      <b>{heading}</b>
      <span>{children}</span>
    </div>
  );
}
