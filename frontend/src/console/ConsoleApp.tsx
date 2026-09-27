import { lazy, Suspense, useEffect, useState } from 'react';
import { Link, NavLink, Outlet, useLocation, useNavigate, useParams } from 'react-router-dom';
import { logout } from '../api/endpoints';
import { useSession } from '../auth/session.tsx';
import { useBoot } from './Boot.tsx';
import { CircuitsSheet } from './circuits/CircuitsSheet.tsx';
import { ComposerSheet } from './composer/ComposerSheet.tsx';
import { groupDigits } from './derive/format.ts';
import { JournalSheet } from './journal/JournalSheet.tsx';
import { PRODUCT } from './product.ts';
import { ConsoleProvider, useConsole } from './state.tsx';
import { Mark } from './ui/Mark.tsx';
import { Skeleton } from './ui/States.tsx';
import './console.css';

// DOOM and its terminal load only when someone finds the way in.
const TerminalSheet = lazy(() => import('./terminal/TerminalSheet.tsx').then((module) => ({ default: module.TerminalSheet })));

function sheetOf(pathname: string): string {
  if (pathname.startsWith('/journal')) return 'Match journal';
  if (pathname.startsWith('/terminal')) return 'Service terminal';
  if (pathname.startsWith('/circuits/new')) return 'New circuit';
  if (/^\/circuits\/\d+/.test(pathname)) return 'Edit circuit';
  return 'Circuits';
}

function Readouts() {
  const { engine } = useConsole();
  if (engine.data) {
    const { chains, rules, match_count } = engine.data;
    return (
      <div className="sh-r">
        <span>
          WINDOW{' '}
          <b>
            {chains
              .map((c) => `${c.name} · blocks ${groupDigits(String(c.first_block))}–${groupDigits(String(c.last_block))}`)
              .join('; ')}
          </b>
        </span>
        <span>
          ENGINE{' '}
          <b>
            {rules.enabled} of {rules.total} circuits armed · {match_count} matches
          </b>
        </span>
      </div>
    );
  }
  if (engine.status === 'error') {
    return (
      <div className="sh-r" role="alert">
        <span className="sh-err" title={engine.error}>
          ENGINE STATUS UNAVAILABLE
        </span>
        <button type="button" className="btn sm" onClick={engine.reload}>
          Retry
        </button>
      </div>
    );
  }
  return (
    <div className="sh-r" role="status" aria-label="Loading the engine status">
      <span>
        WINDOW <span className="skel-line inline" />
      </span>
      <span>
        ENGINE <span className="skel-line inline" />
      </span>
    </div>
  );
}

/** Who is signed in, and the way out. Stays in the header at every width. */
function Operator() {
  const { operator } = useSession();
  const navigate = useNavigate();
  const [leaving, setLeaving] = useState(false);
  const signOut = async () => {
    if (leaving) return;
    setLeaving(true);
    try {
      await logout();
    } catch {
      // The session may already be gone; sign-in is the right place either way.
    }
    navigate('/signin', { replace: true, state: { signedOut: true } });
  };
  return (
    <div className="op">
      {operator && (
        <span>
          <span className="lbl-op">OPERATOR </span>
          <b title={operator}>{operator}</b>
        </span>
      )}
      <button type="button" className="btn sm" onClick={() => void signOut()} disabled={leaving} aria-busy={leaving}>
        {leaving ? 'SIGNING OUT…' : 'SIGN OUT'}
      </button>
    </div>
  );
}

function Shell() {
  const { engine, rules, toast, selection, journalSearch, journalKeys } = useConsole();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const sheet = sheetOf(pathname);
  const onJournal = pathname.startsWith('/journal');
  const onTerminal = pathname.startsWith('/terminal');
  const onCircuits = !onJournal && !onTerminal;

  useEffect(() => {
    document.title = `${sheet} · ${PRODUCT}`;
  }, [sheet]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      // The terminal's keys belong to its prompt and to DOOM.
      if (onTerminal || target.closest('input, select, textarea') || event.metaKey || event.ctrlKey || event.altKey) return;
      const keys = journalKeys.current;
      const actions: Record<string, (() => void) | undefined> = {
        '/': keys?.focusSearch,
        j: keys?.next,
        k: keys?.prev,
        '[': keys ? () => keys.channel(-1) : undefined,
        ']': keys ? () => keys.channel(1) : undefined,
        r: keys?.replay,
      };
      if (!(event.key in actions)) return;
      if (onJournal && keys) {
        event.preventDefault();
        actions[event.key]?.();
      } else if (event.key !== '/') {
        // Journal keys elsewhere bring the journal back, as it was left.
        event.preventDefault();
        navigate(`/journal/${journalSearch}`);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onJournal, onTerminal, journalKeys, journalSearch, navigate]);

  const boot = useBoot();

  return (
    <div className="phosphor">
      <div className="dev">
        <div className="screen">
          <div className={boot.className} style={boot.style}>
            {boot.overlay}
            <header className="sh">
              <div className="logo">
                <Mark />
                PHOSPHOR
              </div>
              <Readouts />
              <Operator />
            </header>
            <nav className="stabs" aria-label="Sections">
              <NavLink to={`/journal/${journalSearch}`} className={() => (onJournal ? 'tab active' : 'tab')} aria-current={onJournal ? 'page' : undefined}>
                JOURNAL <span className="n">{engine.data?.match_count ?? ''}</span>
              </NavLink>
              <NavLink to="/circuits/" className={() => (onCircuits ? 'tab active' : 'tab')} aria-current={onCircuits ? 'page' : undefined}>
                CIRCUITS <span className="n">{rules.data?.length ?? ''}</span>
              </NavLink>
              {onTerminal && (
                <span className="tab active" aria-current="page">
                  TERMINAL
                </span>
              )}
              <span className="sp" />
              <Link to="/circuits/new/" className="newc">
                NEW<span className="nw-long"> CIRCUIT</span>
              </Link>
            </nav>
            <main className="main">
              <Outlet />
            </main>
            <footer className="sl">
              <span>
                SHEET <b>{sheet}</b>
              </span>
              <span>
                SEL <b>{onJournal ? selection : '—'}</b>
              </span>
              <span className="hint">
                {onTerminal
                  ? 'ARROWS/WASD MOVE · CTRL/CLICK FIRE · SPACE/E USE · SHIFT RUN · ESC MENU'
                  : '/ SEARCH · J/K NEXT MATCH · [ ] CHANNEL · R REPLAY'}
              </span>
              {/* The way into the service terminal: the cursor itself. */}
              <Link to="/terminal/" className="blink trap" aria-label="Service terminal">
                █
              </Link>
            </footer>
          </div>
        </div>
      </div>
      <div className="toast" role="status" hidden={!toast} key={toast?.key}>
        {toast?.message}
      </div>
    </div>
  );
}

/** The console behind sign-in: one shell, and a sheet per route. */
export function ConsoleApp() {
  return (
    <ConsoleProvider>
      <Shell />
    </ConsoleProvider>
  );
}

/** The service terminal, fetched the first time it opens. */
export function TerminalRoute() {
  return (
    <Suspense fallback={<Skeleton rows={2} lines={3} label="Opening the service terminal" />}>
      <TerminalSheet />
    </Suspense>
  );
}

/** A fresh composer per circuit, so moving between two edits never carries a draft over. */
export function ComposerRoute() {
  const { id } = useParams();
  return <ComposerSheet key={id ?? 'new'} />;
}

export { CircuitsSheet, JournalSheet };
