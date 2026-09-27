import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, ApiError, errorMessage } from '../api/index.ts';
import type { Backtest, BacktestRow, ConditionNode, Glyph as GlyphName, Rule } from '../api/types.ts';
import { Circuit } from '../circuit/Circuit.tsx';
import { Glyph } from '../circuit/Glyph.tsx';
import type { Comparison, Group } from '../derive/describe.ts';
import { formatUnits, groupDigits, plural, rawMagnitude, short } from '../derive/format.ts';
import { normalizeTag, tagProblem, TAG_MAX } from '../derive/tags.ts';
import { addSibling, findNode, forWrite, isComplete, leaves, removeNode, replaceNode, withDraftIds } from '../derive/tree.ts';
import { tokenValues, useConsole } from '../state.tsx';
import { Icon } from '../ui/Icon.tsx';
import { EmptyState, ErrorState, Skeleton } from '../ui/States.tsx';
import { type AddressList, GateEditor } from './GateEditor.tsx';

const GLYPHS: GlyphName[] = [
  'triangle', 'diamond', 'target', 'square', 'star', 'bars',
  'chevron', 'bolt', 'hexagon', 'circle', 'xmark', 'ring',
];
const EXAMPLES = [
  'USDT transfers over 10k',
  'Stablecoins leaving Binance',
  'ETH over 20',
  'Transfers of unknown tokens',
  'PEPE or LINK above 1m',
];
const FIRST_SENTENCE = EXAMPLES[0];

type Draft = {
  id: number | null;
  /** The sentence last wired, which is what gets saved. */
  sentence: string;
  /** What the proposal read; null before any wiring, [] when nothing was understood. */
  understood: string[] | null;
  condition: Group | null;
  selected: number | null;
  tag: string;
  tagTouched: boolean;
  glyph: GlyphName;
  glyphTouched: boolean;
  name: string;
  preview: string | null;
};

type Run = { status: 'idle' | 'loading' | 'error'; error: string };

function rowAmount(row: BacktestRow): { amount: string; unit: string } {
  const { headline } = row;
  if (headline.kind === 'native') return { amount: formatUnits(headline.amount.raw, 18, 4), unit: 'ETH' };
  const { raw, decimals } = headline.amount;
  return {
    amount: decimals === null ? `${rawMagnitude(raw)} raw` : formatUnits(raw, decimals),
    unit: headline.token?.symbol ?? 'Unknown token',
  };
}

export function ComposerSheet() {
  const { id: idParam } = useParams();
  const editId = idParam && /^\d+$/.test(idParam) ? Number(idParam) : null;
  const { rules, vocabulary, engine, describer, ensureTokens, refresh, showToast } = useConsole();
  const navigate = useNavigate();
  const nextId = useRef(-1);
  const draftId = () => nextId.current--;

  const [draft, setDraft] = useState<Draft | null>(null);
  const [original, setOriginal] = useState<Rule | null>(null);
  const [load, setLoad] = useState<Run>({ status: 'loading', error: '' });
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [sentenceInput, setSentenceInput] = useState(editId === null ? FIRST_SENTENCE : '');
  const [wiring, setWiring] = useState<Run>({ status: 'idle', error: '' });
  const [saving, setSaving] = useState<Run>({ status: 'idle', error: '' });

  const wire = async (sentence: string) => {
    setWiring({ status: 'loading', error: '' });
    try {
      const proposal = await api.propose(sentence);
      await ensureTokens(tokenValues([proposal.condition]));
      const condition = withDraftIds(proposal.condition, draftId) as Group;
      setDraft((current) =>
        current && {
          ...current,
          sentence,
          understood: proposal.understood,
          condition,
          selected: null,
          preview: null,
          name: !current.name || current.id === null ? proposal.name : current.name,
          tag: current.tagTouched ? current.tag : proposal.tag,
          glyph: current.tagTouched || current.glyphTouched ? current.glyph : proposal.glyph,
        },
      );
      setWiring({ status: 'idle', error: '' });
    } catch (error) {
      if (error instanceof ApiError && error.code === 'not_understood') {
        setDraft((current) => current && { ...current, sentence, understood: [], condition: null, selected: null, preview: null });
        setWiring({ status: 'idle', error: '' });
      } else {
        setWiring({ status: 'error', error: errorMessage(error) });
      }
    }
  };

  useEffect(() => {
    let live = true;
    if (editId === null) {
      setDraft({
        id: null, sentence: FIRST_SENTENCE, understood: null, condition: null, selected: null,
        tag: '', tagTouched: false, glyph: GLYPHS[0], glyphTouched: false, name: '', preview: null,
      });
      setLoad({ status: 'idle', error: '' });
      void wire(FIRST_SENTENCE);
      return;
    }
    setLoad({ status: 'loading', error: '' });
    api
      .getRule(editId)
      .then(async (rule) => {
        await ensureTokens(tokenValues([rule.condition]));
        if (!live) return;
        setOriginal(rule);
        setSentenceInput(rule.sentence);
        setDraft({
          id: rule.id, sentence: rule.sentence, understood: null, condition: rule.condition as Group, selected: null,
          tag: rule.tag, tagTouched: true, glyph: rule.glyph, glyphTouched: true, name: rule.name, preview: null,
        });
        setLoad({ status: 'idle', error: '' });
      })
      .catch((error: unknown) => live && setLoad({ status: 'error', error: errorMessage(error) }));
    return () => {
      live = false;
    };
    // Loads once per circuit (and on retry), not whenever `wire` is re-created.
  }, [editId, loadAttempt]);

  const condition = draft?.condition ?? null;
  const complete = condition !== null && isComplete(condition, vocabulary.data);
  const conditionKey = complete ? JSON.stringify(condition) : null;
  const [backtest, setBacktest] = useState<{ run: Run; data: Backtest | null; key: string | null }>({
    run: { status: 'idle', error: '' },
    data: null,
    key: null,
  });
  const [backtestAttempt, setBacktestAttempt] = useState(0);
  useEffect(() => {
    if (!conditionKey) return;
    let live = true;
    setBacktest((current) => ({ ...current, run: { status: 'loading', error: '' } }));
    const timer = window.setTimeout(() => {
      api.backtest(JSON.parse(conditionKey) as ConditionNode).then(
        (data) => live && setBacktest({ run: { status: 'idle', error: '' }, data, key: conditionKey }),
        (error: unknown) =>
          live && setBacktest({ run: { status: 'error', error: errorMessage(error) }, data: null, key: null }),
      );
    }, 250);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [conditionKey, backtestAttempt]);

  const fresh = backtest.key !== null && backtest.key === conditionKey ? backtest.data : null;
  const shownBacktest = complete ? fresh ?? backtest.data : null;
  const previewRow =
    fresh && fresh.matches.length
      ? fresh.matches.find((row) => row.transaction.hash === draft?.preview) ?? fresh.matches[0]
      : null;

  const others = (rules.data ?? []).filter((rule) => rule.id !== draft?.id);
  const usedGlyphs = new Set(others.map((rule) => rule.glyph));
  const lists = useMemo<AddressList[]>(() => {
    const seen = new Map<string, AddressList>();
    const all = [...(rules.data ?? []).map((rule) => rule.condition), ...(condition ? [condition] : [])];
    for (const node of all.flatMap(leaves)) {
      if (node.operator !== 'in') continue;
      const list = node.value as AddressList;
      seen.set(`${list.name ?? ''}|${list.addresses.join()}`, list);
    }
    return [...seen.values()];
  }, [rules.data, condition]);
  const chain = engine.data?.chains[0]?.chain ?? 1;

  if (load.status === 'error') {
    return (
      <section className="pane sheet" aria-label="Circuit composer">
        <div className="pane-h">
          <h1>Edit circuit</h1>
        </div>
        <ErrorState what="Couldn't load this circuit." error={load.error} onRetry={() => setLoadAttempt((n) => n + 1)} />
      </section>
    );
  }
  if (!draft) {
    return (
      <section className="pane sheet" aria-label="Circuit composer">
        <div className="pane-h">
          <h1>Edit circuit</h1>
        </div>
        <Skeleton rows={4} lines={3} label="Loading the circuit" />
      </section>
    );
  }

  const update = (patch: Partial<Draft>) => setDraft((current) => current && { ...current, ...patch });
  const selectedNode = condition && draft.selected !== null ? findNode(condition, draft.selected) : null;
  const problem = tagProblem(draft.tag, rules.data ?? [], draft.id);
  const canSave = complete && draft.name.trim() !== '' && problem === '' && saving.status !== 'loading';

  const submitSentence = (event: FormEvent) => {
    event.preventDefault();
    if (sentenceInput.trim()) void wire(sentenceInput);
  };

  const addGate = (kind: 'and' | 'or') => {
    if (!condition || draft.selected === null || !selectedNode || selectedNode.type !== 'comparison') return;
    const fresh: Comparison =
      selectedNode.source === 'token_transfer'
        ? { id: draftId(), type: 'comparison', source: 'token_transfer', field: 'amount', operator: 'gte', value: '' }
        : { id: draftId(), type: 'comparison', source: 'transaction', field: 'value', operator: 'gte', value: '' };
    update({ condition: addSibling(condition, draft.selected, kind, fresh, draftId), selected: fresh.id });
    window.setTimeout(() => document.querySelector<HTMLElement>('.editor [id$="-value"]')?.focus(), 0);
  };

  const save = async () => {
    if (!condition || !canSave) return;
    setSaving({ status: 'loading', error: '' });
    const body = {
      name: draft.name.trim(),
      tag: draft.tag,
      glyph: draft.glyph,
      sentence: draft.sentence,
      condition: forWrite(condition),
    };
    try {
      const saved = draft.id === null ? await api.createRule({ ...body, enabled: true }) : await api.updateRule(draft.id, body);
      refresh();
      const count = saved.stats.match_count;
      navigate(`/journal/?channel=${saved.id}`);
      showToast(`Saved ${saved.tag}. ${count} ${plural(count, 'match', 'matches')} in the ingested blocks.`);
    } catch (error) {
      setSaving({ status: 'error', error: errorMessage(error) });
    }
  };

  const window0 = engine.data?.chains[0] ?? backtest.data?.window;
  const windowLabel = window0
    ? `Blocks ${groupDigits(String(window0.first_block))}–${String(window0.last_block).slice(-3)}`
    : 'Ingested blocks';

  return (
    <div className="compose">
      <section className="pane" aria-label="Circuit composer">
        <div className="pane-h">
          <h1>{draft.id === null ? 'New circuit' : `Edit ${original?.tag ?? draft.tag}`}</h1>
          <span className="lbl">Describe it, check the wiring, arm it</span>
        </div>
        <div className="scroll">
          <form className="sentence-row" onSubmit={submitSentence}>
            <label htmlFor="sentence">Describe the transactions to catch</label>
            <div className="sin">
              <input
                id="sentence"
                autoComplete="off"
                value={sentenceInput}
                placeholder="e.g. USDC transfers over 250k to 0x…"
                onChange={(event) => setSentenceInput(event.target.value)}
              />
              <button type="submit" className={`btn${condition ? '' : ' primary'}`} disabled={wiring.status === 'loading'}>
                {wiring.status === 'loading' ? 'Wiring…' : 'Wire circuit'}
              </button>
            </div>
            <div className="examples">
              Try
              {EXAMPLES.map((example) => (
                <button
                  type="button"
                  key={example}
                  className="chip"
                  onClick={() => {
                    setSentenceInput(example);
                    void wire(example);
                  }}
                >
                  {example}
                </button>
              ))}
            </div>
            {draft.understood !== null &&
              (condition && draft.understood.length ? (
                <div className="parsed">
                  Read as
                  {draft.understood.map((part, i) => (
                    <span key={i} className="tag">
                      {part}
                    </span>
                  ))}
                  <span className="dim">In production the LLM proposes this wiring. Check each gate before arming.</span>
                </div>
              ) : !condition ? (
                <div className="parsed abn" role="alert">
                  <Icon name="warn" /> Couldn't turn that into conditions. Name a token, an amount such as “over 10k”, or an
                  address after “from” or “to”.
                </div>
              ) : null)}
            {wiring.status === 'error' && (
              <ErrorState what="Couldn't wire that sentence." error={wiring.error} onRetry={() => void wire(sentenceInput)} />
            )}
          </form>
          {condition ? (
            <>
              <Circuit
                className="editor-rung"
                condition={condition}
                trace={previewRow?.trace ?? null}
                tag={draft.tag || 'DRAFT'}
                glyph={draft.glyph}
                describer={describer}
                mode="edit"
                selected={draft.selected}
                onPick={(pick) => pick !== 'coil' && update({ selected: pick })}
                label="Draft circuit. Select a gate to edit it."
              />
              <div className="legend">
                {previewRow ? (
                  <span>
                    <i className="sw" />
                    Lit for the selected backtest transaction
                  </span>
                ) : fresh ? (
                  <span>No transaction in the window energises this circuit yet.</span>
                ) : null}
                <span>Select a gate to edit it</span>
              </div>
              {selectedNode && selectedNode.type === 'comparison' ? (
                <GateEditor
                  node={selectedNode}
                  vocabulary={vocabulary}
                  chain={chain}
                  lists={lists}
                  canRemove={leaves(condition).length > 1}
                  onChange={(next) => update({ condition: replaceNode(condition, next.id!, next) as Group })}
                  onAdd={addGate}
                  onRemove={() => update({ condition: removeNode(condition, draft.selected!, draftId), selected: null })}
                />
              ) : (
                <div className="editor">
                  <div className="acts">
                    <span className="hint">Select a gate on the circuit to change it or to wire more gates next to it.</span>
                  </div>
                </div>
              )}
            </>
          ) : wiring.status === 'loading' ? (
            <Skeleton rows={2} lines={3} label="Wiring the circuit" />
          ) : (
            <EmptyState title="Start with a sentence.">
              Describe what should match, such as a token, an amount and a counterparty. It is wired as a circuit you can
              check and edit before arming.
            </EmptyState>
          )}
        </div>
      </section>
      <aside className="pane" aria-label="Backtest">
        <div className="pane-h">
          <h2>Backtest</h2>
          <span className="lbl">{windowLabel}</span>
        </div>
        <div className="scroll aside-body">
          <div className="bt-sum" aria-busy={backtest.run.status === 'loading'}>
            {backtest.run.status === 'error' && complete ? (
              <ErrorState what="Couldn't run the backtest." error={backtest.run.error} onRetry={() => setBacktestAttempt((n) => n + 1)} />
            ) : (
              <>
                <div className={`count${shownBacktest && !fresh ? ' stale' : ''}`}>{shownBacktest ? shownBacktest.match_count : '—'}</div>
                <div className="bt-cap">
                  {!complete
                    ? 'Complete every condition to run the backtest'
                    : shownBacktest
                      ? `of ${shownBacktest.transactions_scanned} ingested transactions would energise this circuit`
                      : 'Running the backtest…'}
                </div>
                {shownBacktest && shownBacktest.unevaluable_count > 0 && (
                  <div className="note abn">
                    <Icon name="warn" />
                    <span>
                      {shownBacktest.unevaluable_count} couldn't be evaluated because the token's decimals are unknown.
                    </span>
                  </div>
                )}
              </>
            )}
          </div>
          {shownBacktest && shownBacktest.matches.length > 0 && (
            <div className={`bt-list${fresh ? '' : ' stale'}`}>
              {shownBacktest.matches.map((row) => {
                const { amount, unit } = rowAmount(row);
                const tx = row.transaction;
                return (
                  <button
                    type="button"
                    key={tx.hash}
                    className="bt-row"
                    aria-pressed={previewRow?.transaction.hash === tx.hash}
                    onClick={() => update({ preview: tx.hash })}
                  >
                    <span>
                      <b>{amount}</b> {unit}
                      <br />
                      <span className="fl">
                        {short(row.headline.from_address)} → {short(row.headline.to_address)}
                      </span>
                    </span>
                    <span className="mono dim">
                      {groupDigits(String(tx.block_number))}·{tx.transaction_index}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
          <div className="namefield">
            <label htmlFor="rule-tag">
              Tag
              <input
                id="rule-tag"
                value={draft.tag}
                maxLength={TAG_MAX}
                spellCheck={false}
                autoComplete="off"
                aria-describedby="tag-err"
                aria-invalid={problem !== ''}
                onChange={(event) => update({ tag: normalizeTag(event.target.value), tagTouched: true })}
              />
            </label>
            <p className="tag-err" id="tag-err" role="status">
              {problem}
            </p>
            <fieldset className="glyphs">
              <legend>Glyph</legend>
              {GLYPHS.map((glyph) => {
                const used = usedGlyphs.has(glyph);
                return (
                  <button
                    type="button"
                    key={glyph}
                    className="gbtn"
                    aria-pressed={draft.glyph === glyph}
                    aria-label={`${glyph}${used ? ' (used by another circuit)' : ''}`}
                    data-used={used || undefined}
                    onClick={() => update({ glyph, glyphTouched: true })}
                  >
                    <Glyph name={glyph} />
                  </button>
                );
              })}
            </fieldset>
            <label htmlFor="rule-name">
              Circuit name
              <input
                id="rule-name"
                value={draft.name}
                placeholder="Name this circuit"
                onChange={(event) => update({ name: event.target.value })}
              />
            </label>
          </div>
        </div>
        <div className="saverow">
          <button type="button" className="btn primary" disabled={!canSave} onClick={() => void save()}>
            {saving.status === 'loading' ? 'Saving…' : draft.id === null ? 'Save and arm' : 'Save changes'}
          </button>
          <button type="button" className="btn" onClick={() => navigate(draft.id === null ? '/journal/' : '/circuits/')}>
            Cancel
          </button>
          {saving.status === 'error' && (
            <p className="save-err" role="alert">
              {saving.error}
            </p>
          )}
        </div>
      </aside>
    </div>
  );
}
