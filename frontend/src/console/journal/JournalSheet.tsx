import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type RefObject } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { api, errorMessage } from '../api/index.ts';
import type { JournalRow, Rule } from '../api/types.ts';
import { Glyph, Ident } from '../circuit/Glyph.tsx';
import { channelMatches, showsAll, tuneTarget } from '../derive/channels.ts';
import { formatUnits, groupDigits, rawMagnitude, short, utcClock } from '../derive/format.ts';
import { tokenValues, useConsole, useResource } from '../state.tsx';
import { Icon } from '../ui/Icon.tsx';
import { EmptyState, ErrorState, Skeleton } from '../ui/States.tsx';
import { type Inspect, TraceView } from './Trace.tsx';

type Channel = 'all' | number;

type Journal = {
  /** The channel these rows belong to, so a new channel never acts on the last one's rows. */
  channel: Channel;
  status: 'loading' | 'ready' | 'error';
  rows: JournalRow[];
  next: string | null;
  error: string;
  more: 'idle' | 'loading' | 'error';
};

const idParam = (value: string | null): number | null => (value && /^\d+$/.test(value) ? Number(value) : null);

/** The amount a journal row leads with. An amount with unknown decimals is never scaled. */
function rowAmount(row: JournalRow): { amount: string; unit: string | null } {
  const { headline } = row;
  if (headline.kind === 'native') return { amount: formatUnits(headline.amount.raw, 18, 4), unit: 'ETH' };
  const { raw, decimals } = headline.amount;
  return {
    amount: decimals === null ? `${rawMagnitude(raw)} raw` : formatUnits(raw, decimals),
    unit: headline.token?.symbol ?? null,
  };
}

function Channels({
  rules,
  channel,
  query,
  setQuery,
  onTune,
  inputRef,
}: {
  rules: ReturnType<typeof useConsole>['rules'];
  channel: Channel;
  query: string;
  setQuery: (value: string) => void;
  onTune: (channel: Channel) => void;
  inputRef: RefObject<HTMLInputElement>;
}) {
  const list = rules.data ? channelMatches(rules.data, query) : [];
  const total = (rules.data ?? []).filter((r) => r.enabled).reduce((sum, r) => sum + r.stats.match_count, 0);
  const all = showsAll(query);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const target = tuneTarget(rules.data ?? [], query);
    if (target === null) return;
    onTune(target);
    setQuery('');
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== 'Escape') return;
    setQuery('');
    event.currentTarget.blur();
  };
  return (
    <section className="pane cpane" aria-label="Channels">
      <div className="pane-h">
        <h1>Channels</h1>
        <span className="lbl">/ to search</span>
      </div>
      <form className="cmd" role="search" onSubmit={submit}>
        <label htmlFor="cmd" className="prompt">
          &gt;
        </label>
        <input
          id="cmd"
          ref={inputRef}
          autoComplete="off"
          spellCheck={false}
          placeholder="tune bnb"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={onKeyDown}
          aria-label="Search circuits. Enter tunes the journal to the top result."
        />
      </form>
      <div className="scroll chans" role="group" aria-label="Filter journal by circuit">
        {rules.data ? (
          <>
            {all && (
              <button type="button" className="chan" aria-pressed={channel === 'all'} onClick={() => onTune('all')}>
                <span className="gl-slot">
                  <Glyph name="ring" />
                </span>
                <span className="ct">
                  <b>ALL</b>
                  <small>Every armed circuit</small>
                </span>
                <span className="cn">{total}</span>
              </button>
            )}
            {list.map((rule, i) => (
              <button
                type="button"
                key={rule.id}
                className={`chan${rule.enabled ? '' : ' off'}${query.trim() && i === 0 ? ' top' : ''}`}
                aria-pressed={channel === rule.id}
                onClick={() => onTune(rule.id)}
              >
                <span className="gl-slot">
                  <Glyph name={rule.glyph} />
                </span>
                <span className="ct">
                  <b>{rule.tag}</b>
                  <small>{rule.name}</small>
                </span>
                <span className="cn">{rule.enabled ? rule.stats.match_count : 'OFF'}</span>
              </button>
            ))}
            {!list.length && !all && <p className="ch-empty">No circuit matches “{query}”.</p>}
          </>
        ) : rules.status === 'error' ? (
          <ErrorState what="Couldn't load the circuits." error={rules.error} onRetry={rules.reload} />
        ) : (
          <Skeleton rows={6} label="Loading channels" />
        )}
      </div>
    </section>
  );
}

function JournalList({
  journal,
  channel,
  rule,
  total,
  selectedId,
  onSelect,
  onMove,
  onRetry,
  onMore,
}: {
  journal: Journal;
  channel: Channel;
  rule: Rule | undefined;
  total: number;
  selectedId: number | null;
  onSelect: (row: JournalRow) => void;
  onMove: (step: 1 | -1) => void;
  onRetry: () => void;
  onMore: () => void;
}) {
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    event.preventDefault();
    onMove(event.key === 'ArrowDown' ? 1 : -1);
  };
  const focusable = journal.rows.some((row) => row.id === selectedId) ? selectedId : journal.rows[0]?.id;
  let body;
  if (journal.status === 'loading') body = <Skeleton rows={7} lines={3} label="Loading matches" />;
  else if (journal.status === 'error') {
    body = <ErrorState what="Couldn't load the match journal." error={journal.error} onRetry={onRetry} />;
  } else if (!journal.rows.length) {
    body =
      channel === 'all' ? (
        <EmptyState title="No matches yet.">New blocks are checked as they are ingested.</EmptyState>
      ) : (
        <EmptyState title="No matches for this circuit.">
          {rule && !rule.enabled
            ? "It's switched off, so it isn't tested. Arm it on the Circuits sheet."
            : 'New blocks are checked as they are ingested. Try widening the circuit.'}
        </EmptyState>
      );
  } else {
    body = (
      <div role="listbox" aria-label="Matches" onKeyDown={onKeyDown}>
        {journal.rows.map((row) => {
          const { amount, unit } = rowAmount(row);
          const { headline, transaction: tx } = row;
          const abnormal = row.flags.token_unrecognised || row.flags.decimals_unknown;
          return (
            <button
              type="button"
              key={row.id}
              className="jrow j2"
              role="option"
              data-id={row.id}
              aria-selected={row.id === selectedId}
              tabIndex={row.id === focusable ? 0 : -1}
              onClick={() => onSelect(row)}
            >
              <span className="c-id">
                <Ident rule={row.rule} />
              </span>
              <span className="c-amt">
                {amount}
                {unit && <span className="u">{unit}</span>}
              </span>
              <span className="flag">
                {abnormal && (
                  <span className="abn" title="Unknown token or decimals">
                    <Icon name="warn" />
                  </span>
                )}
              </span>
              <span className="c-fl">
                {headline.from_label || short(headline.from_address)} →{' '}
                {headline.to_address ? headline.to_label || short(headline.to_address) : 'new contract'}
              </span>
              <span className="c-meta">
                {utcClock(tx.block_timestamp)} UTC · block {groupDigits(String(tx.block_number))} · tx {tx.transaction_index}
              </span>
            </button>
          );
        })}
        {journal.next && (
          <div className="more">
            <button type="button" className="btn sm" onClick={onMore} disabled={journal.more === 'loading'}>
              {journal.more === 'loading' ? 'Loading…' : 'Load older matches'}
            </button>
            {journal.more === 'error' && <span className="lbl">Couldn't load more. Try again.</span>}
          </div>
        )}
      </div>
    );
  }
  return (
    <section className="pane jpane" aria-label="Match journal">
      <div className="pane-h">
        <h1>Match journal</h1>
        <span className="lbl">
          {channel === 'all' ? 'ALL · ' : rule ? (
            <>
              <Ident rule={rule} /> ·{' '}
            </>
          ) : (
            'UNKNOWN CIRCUIT · '
          )}
          {total} · newest first
        </span>
      </div>
      <div className="jhead j2" aria-hidden="true">
        <span className="lbl">Circuit</span>
        <span className="lbl">Amount</span>
        <span />
      </div>
      <div className="scroll jlist">{body}</div>
    </section>
  );
}

export function JournalSheet() {
  const {
    rules,
    describer,
    ensureTokens,
    showToast,
    setSelection,
    setJournalSearch,
    journalKeys,
    playedOnce,
  } = useConsole();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const channel: Channel = idParam(params.get('channel')) ?? 'all';
  const selectedId = idParam(params.get('match'));
  const rule = channel === 'all' ? undefined : rules.data?.find((r) => r.id === channel);
  const total =
    channel === 'all'
      ? (rules.data ?? []).filter((r) => r.enabled).reduce((sum, r) => sum + r.stats.match_count, 0)
      : rule?.enabled
        ? rule.stats.match_count
        : 0;

  useEffect(() => setJournalSearch(params.toString() ? `?${params.toString()}` : ''), [params, setJournalSearch]);

  const [journal, setJournal] = useState<Journal>({ channel, status: 'loading', rows: [], next: null, error: '', more: 'idle' });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setJournal({ channel, status: 'loading', rows: [], next: null, error: '', more: 'idle' });
    api.matches({ rule: channel === 'all' ? undefined : channel }).then(
      (page) => live && setJournal({ channel, status: 'ready', rows: page.results, next: page.next, error: '', more: 'idle' }),
      (error: unknown) =>
        live && setJournal({ channel, status: 'error', rows: [], next: null, error: errorMessage(error), more: 'idle' }),
    );
    return () => {
      live = false;
    };
  }, [channel, attempt]);

  const loadMore = () => {
    if (!journal.next) return;
    const asked = channel;
    const land = (update: (current: Journal) => Journal) =>
      setJournal((current) => (current.channel === asked ? update(current) : current));
    land((current) => ({ ...current, more: 'loading' }));
    api.matches({ rule: asked === 'all' ? undefined : asked, cursor: journal.next }).then(
      (page) => land((current) => ({ ...current, rows: [...current.rows, ...page.results], next: page.next, more: 'idle' })),
      () => land((current) => ({ ...current, more: 'error' })),
    );
  };

  const [play, setPlay] = useState<{ token: number; matchId: number } | null>(null);
  const [inspect, setInspect] = useState<Inspect>('coil');
  const [pinned, setPinned] = useState(false);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const cmdRef = useRef<HTMLInputElement>(null);
  const focusRow = useRef<number | null>(null);

  const replayFor = (matchId: number) => setPlay((current) => ({ token: (current?.token ?? 0) + 1, matchId }));

  const select = useCallback(
    (id: number, options: { open?: boolean; all?: boolean } = {}) => {
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.set('match', String(id));
          if (options.all) next.delete('channel');
          return next;
        },
        { replace: true },
      );
      setInspect('coil');
      setPinned(false);
      setPlay((current) => ({ token: (current?.token ?? 0) + 1, matchId: id }));
      if (options.open) setOpen(true);
    },
    [setParams],
  );

  // Nothing selected yet (first visit, or a new channel): open the newest match.
  useEffect(() => {
    if (journal.channel !== channel || journal.status !== 'ready' || selectedId !== null || !journal.rows.length) return;
    playedOnce.current = true;
    select(journal.rows[0].id);
  }, [journal, channel, selectedId, select, playedOnce]);

  // A link straight to a match plays it on the session's first journal load.
  useEffect(() => {
    if (selectedId !== null && !playedOnce.current) {
      playedOnce.current = true;
      replayFor(selectedId);
    }
    // On mount only: later selections play through select().
  }, []);

  const detail = useResource(async () => {
    if (selectedId === null) return null;
    const found = await api.matchDetail(selectedId);
    await ensureTokens(tokenValues([found.condition]));
    return found;
  }, [selectedId]);
  const current = detail.data && detail.data.id === selectedId ? detail.data : null;

  useEffect(() => {
    setSelection(
      current
        ? `${current.rule.tag} · ${groupDigits(String(current.transaction.block_number))}/${current.transaction.transaction_index}`
        : '—',
    );
  }, [current, setSelection]);
  useEffect(() => () => setSelection('—'), [setSelection]);

  const tune = (target: Channel) => {
    setParams(target === 'all' ? {} : { channel: String(target) }, { replace: true });
  };

  const move = (step: 1 | -1, focus = false) => {
    const rows = journal.rows;
    if (!rows.length) return;
    const index = rows.findIndex((row) => row.id === selectedId);
    const next = rows[Math.min(rows.length - 1, Math.max(0, index + step))];
    if (!next || next.id === selectedId) return;
    select(next.id);
    if (focus) focusRow.current = next.id;
    document.querySelector(`.jrow[data-id="${next.id}"]`)?.scrollIntoView({ block: 'nearest' });
  };

  useEffect(() => {
    if (focusRow.current === null) return;
    document.querySelector<HTMLElement>(`.jrow[data-id="${focusRow.current}"]`)?.focus();
    focusRow.current = null;
  });

  useEffect(() => {
    journalKeys.current = {
      next: () => move(1),
      prev: () => move(-1),
      channel: (step) => {
        const ids: Channel[] = ['all', ...(rules.data ?? []).filter((r) => r.enabled).map((r) => r.id)];
        const index = ids.indexOf(channel);
        tune(ids[(index + step + ids.length) % ids.length]);
      },
      replay: () => selectedId !== null && replayFor(selectedId),
      focusSearch: () => {
        cmdRef.current?.focus();
        cmdRef.current?.select();
      },
    };
  });
  useEffect(() => () => void (journalKeys.current = null), [journalKeys]);

  const copy = (text: string) => {
    const blocked = () => showToast('Copy was blocked. Select the text instead.');
    try {
      navigator.clipboard.writeText(text).then(() => showToast(`Copied ${short(text)}`), blocked);
    } catch {
      blocked();
    }
  };

  const playKey = play && current && play.matchId === current.id ? `${play.token}:${play.matchId}` : null;

  let trace;
  if (selectedId === null) {
    trace = journal.status === 'loading' ? null : <EmptyState title="Select a match to see its trace." />;
  } else if (current) {
    trace = (
      <TraceView
        detail={current}
        describer={describer}
        playKey={playKey}
        inspect={inspect}
        pinned={pinned}
        onInspect={(pick) => {
          setInspect(pick);
          setPinned(true);
        }}
        onUnpin={() => {
          setInspect('coil');
          setPinned(false);
        }}
        onJump={(matchId) => select(matchId, { all: true })}
        onCopy={copy}
      />
    );
  } else if (detail.status === 'error') {
    trace = <ErrorState what="Couldn't load this match's trace." error={detail.error} onRetry={detail.reload} />;
  }
  const loadingTrace = selectedId !== null && !current && detail.status !== 'error';

  return (
    <div className={`matches${open ? ' detail' : ''}`}>
      <Channels rules={rules} channel={channel} query={query} setQuery={setQuery} onTune={tune} inputRef={cmdRef} />
      <JournalList
        journal={journal.channel === channel ? journal : { ...journal, status: 'loading', rows: [], next: null }}
        channel={channel}
        rule={rule}
        total={total}
        selectedId={selectedId}
        onSelect={(row) => {
          select(row.id, { open: true });
          if (window.matchMedia('(max-width: 860px)').matches) window.scrollTo(0, 0);
        }}
        onMove={(step) => move(step, true)}
        onRetry={() => setAttempt((n) => n + 1)}
        onMore={loadMore}
      />
      <section className="pane tpane" aria-label="Trace" aria-busy={loadingTrace}>
        <div className="pane-h">
          <button type="button" className="btn sm back" onClick={() => setOpen(false)}>
            <Icon name="back" />
            Journal
          </button>
          <h2>Trace</h2>
          <span className="acts-r">
            <button type="button" className="btn sm" onClick={() => selectedId !== null && replayFor(selectedId)} disabled={!current}>
              <Icon name="play" />
              Replay
            </button>
            <button
              type="button"
              className="btn sm"
              onClick={() => current && navigate(`/circuits/${current.rule.id}/`)}
              disabled={!current}
            >
              <Icon name="edit" />
              Edit circuit
            </button>
          </span>
        </div>
        <div className="scroll tscroll">{loadingTrace ? <Skeleton rows={4} lines={3} label="Loading the trace" /> : trace}</div>
      </section>
    </div>
  );
}
