import { useEffect, useId, useState, type KeyboardEvent } from 'react';
import { api, errorMessage } from '../api/index.ts';
import type { ComparisonValue, FieldType, Operator, TokenRef, Vocabulary } from '../api/types.ts';
import { type Comparison, fieldOf, operatorText } from '../derive/describe.ts';
import { short } from '../derive/format.ts';
import { useConsole, type Resource } from '../state.tsx';
import { Icon } from '../ui/Icon.tsx';
import { ErrorState, Skeleton } from '../ui/States.tsx';

export type AddressList = { addresses: string[]; name?: string };

const tokenLabel = (token: TokenRef) => (token.symbol ? `${token.symbol} · ${token.name ?? token.symbol}` : `Unknown · ${short(token.address)}`);

/** A fresh value for a field: blank where the user must say something, the first list for "is in". */
export function defaultValue(type: FieldType, operator: Operator, chain: number, lists: AddressList[]): ComparisonValue {
  if (type === 'token') return { chain, address: '' };
  if (type === 'bool') return false;
  if (type === 'address' && operator === 'in') return lists[0] ? { ...lists[0], addresses: [...lists[0].addresses] } : { addresses: [] };
  return '';
}

/** The token value control: type to search the catalog through `tokens`, pick with the keyboard or pointer. */
function TokenPicker({ id, value, chain, onPick }: { id: string; value: { chain: number; address: string }; chain: number; onPick: (token: TokenRef) => void }) {
  const { tokenOf, learnTokens } = useConsole();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const [results, setResults] = useState<{ status: 'loading' | 'ready' | 'error'; tokens: TokenRef[]; error: string }>({
    status: 'loading',
    tokens: [],
    error: '',
  });
  const [attempt, setAttempt] = useState(0);
  const listId = `${id}-list`;

  useEffect(() => {
    if (!open) return;
    let live = true;
    setResults((current) => ({ ...current, status: 'loading' }));
    const timer = window.setTimeout(() => {
      api.tokens({ q: query, chain }).then(
        (page) => {
          if (!live) return;
          learnTokens(page.results);
          setResults({ status: 'ready', tokens: page.results, error: '' });
          setActive(0);
        },
        (error: unknown) => live && setResults({ status: 'error', tokens: [], error: errorMessage(error) }),
      );
    }, 150);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [open, query, chain, attempt, learnTokens]);

  const current = value.address ? tokenOf(value.chain, value.address) : null;
  const shown = current ? tokenLabel(current) : value.address ? `Unknown · ${short(value.address)}` : '';
  const pick = (token: TokenRef) => {
    onPick(token);
    setOpen(false);
    setQuery('');
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!open) return setOpen(true);
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setActive((index) => Math.max(0, Math.min(results.tokens.length - 1, index + step)));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      const token = results.tokens[active];
      if (open && token) pick(token);
    } else if (event.key === 'Escape') {
      setOpen(false);
      setQuery('');
    }
  };

  return (
    <div className="combo">
      <input
        id={id}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && results.tokens[active] ? `${listId}-${active}` : undefined}
        autoComplete="off"
        spellCheck={false}
        placeholder="Search tokens"
        value={open ? query : shown}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 120)}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
        }}
        onKeyDown={onKeyDown}
      />
      {open && (
        <ul className="combo-list" id={listId} role="listbox" aria-label="Tokens">
          {results.status === 'error' ? (
            <li className="combo-note">
              Couldn't search tokens: {results.error}{' '}
              <button type="button" className="btn sm" onMouseDown={(event) => event.preventDefault()} onClick={() => setAttempt((n) => n + 1)}>
                Retry
              </button>
            </li>
          ) : results.status === 'loading' && !results.tokens.length ? (
            <li className="combo-note">Searching…</li>
          ) : !results.tokens.length ? (
            <li className="combo-note">No token matches “{query}”.</li>
          ) : (
            results.tokens.map((token, index) => (
              <li
                key={`${token.chain}:${token.address}`}
                id={`${listId}-${index}`}
                role="option"
                aria-selected={index === active}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => pick(token)}
              >
                {tokenLabel(token)}
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}

export function GateEditor({
  node,
  vocabulary,
  chain,
  lists,
  canRemove,
  onChange,
  onAdd,
  onRemove,
}: {
  node: Comparison;
  vocabulary: Resource<Vocabulary>;
  chain: number;
  lists: AddressList[];
  canRemove: boolean;
  onChange: (next: Comparison) => void;
  onAdd: (kind: 'and' | 'or') => void;
  onRemove: () => void;
}) {
  const id = useId();
  const vocab = vocabulary.data;
  if (!vocab) {
    return (
      <div className="editor" aria-label="Condition editor">
        {vocabulary.status === 'error' ? (
          <ErrorState what="Couldn't load the vocabulary, so gates can't be edited." error={vocabulary.error} onRetry={vocabulary.reload} />
        ) : (
          <Skeleton rows={1} lines={4} label="Loading the vocabulary" />
        )}
      </div>
    );
  }
  const source = vocab.sources.find((s) => s.key === node.source) ?? vocab.sources[0];
  const field = source.fields.find((f) => f.key === node.field) ?? source.fields[0];
  const type = fieldOf(vocab, node).type;

  const reset = (sourceKey: Comparison['source'], fieldKey: string) => {
    const nextSource = vocab.sources.find((s) => s.key === sourceKey)!;
    const nextField = nextSource.fields.find((f) => f.key === fieldKey) ?? nextSource.fields[0];
    const operator = nextField.operators[0];
    onChange({ ...node, source: sourceKey, field: nextField.key, operator, value: defaultValue(nextField.type, operator, chain, lists) });
  };

  const setOperator = (operator: Operator) => {
    let value = node.value;
    if (type === 'address' && (operator === 'in') !== (node.operator === 'in')) value = defaultValue(type, operator, chain, lists);
    onChange({ ...node, operator, value });
  };

  let control;
  if (type === 'token') {
    const value = typeof node.value === 'object' && 'address' in node.value ? node.value : { chain, address: '' };
    control = <TokenPicker id={`${id}-value`} value={value} chain={chain} onPick={(token) => onChange({ ...node, value: { chain: token.chain, address: token.address } })} />;
  } else if (type === 'bool') {
    control = (
      <select id={`${id}-value`} value={String(node.value)} onChange={(event) => onChange({ ...node, value: event.target.value === 'true' })}>
        <option value="true">recognised</option>
        <option value="false">unrecognised</option>
      </select>
    );
  } else if (type === 'address' && node.operator === 'in') {
    const value = node.value as AddressList;
    const index = lists.findIndex((list) => list.name === value.name && list.addresses.join() === value.addresses.join());
    control = lists.length ? (
      <select id={`${id}-value`} value={index} onChange={(event) => onChange({ ...node, value: { ...lists[Number(event.target.value)] } })}>
        {index < 0 && <option value={-1}>{value.name ?? `${value.addresses.length} addresses`}</option>}
        {lists.map((list, i) => (
          <option key={i} value={i}>
            {list.name ?? `${list.addresses.length} addresses`}
          </option>
        ))}
      </select>
    ) : (
      <textarea
        id={`${id}-value`}
        rows={2}
        spellCheck={false}
        placeholder="0x…, 0x…"
        value={value.addresses.join(', ')}
        onChange={(event) =>
          onChange({ ...node, value: { ...value, addresses: event.target.value.toLowerCase().split(/[\s,]+/).filter(Boolean) } })
        }
      />
    );
  } else if (type === 'amount' || type === 'native_amount') {
    control = (
      <input
        id={`${id}-value`}
        inputMode="decimal"
        autoComplete="off"
        value={String(node.value)}
        onChange={(event) => onChange({ ...node, value: event.target.value.replace(/[,\s]/g, '') })}
      />
    );
  } else {
    const address = type === 'address';
    control = (
      <input
        id={`${id}-value`}
        autoComplete="off"
        spellCheck={false}
        placeholder={address ? '0x…' : 'e.g. transfer'}
        value={String(node.value)}
        onChange={(event) => onChange({ ...node, value: address ? event.target.value.trim().toLowerCase() : event.target.value })}
      />
    );
  }
  const unit = type === 'amount' ? ' (whole tokens)' : type === 'native_amount' ? ' (ETH)' : '';

  return (
    <div className="editor" aria-label="Condition editor">
      <div className="grid">
        <label htmlFor={`${id}-source`}>
          Source
          <select id={`${id}-source`} value={source.key} onChange={(event) => reset(event.target.value as Comparison['source'], '')}>
            {vocab.sources.map((s) => (
              <option key={s.key} value={s.key}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label htmlFor={`${id}-field`}>
          Field
          <select id={`${id}-field`} value={field.key} onChange={(event) => reset(source.key, event.target.value)}>
            {source.fields.map((f) => (
              <option key={f.key} value={f.key}>
                {f.label}
              </option>
            ))}
          </select>
        </label>
        <label htmlFor={`${id}-op`}>
          Operator
          <select id={`${id}-op`} value={node.operator} onChange={(event) => setOperator(event.target.value as Operator)}>
            {field.operators.map((operator) => (
              <option key={operator} value={operator}>
                {operatorText(field.type, operator)}
              </option>
            ))}
          </select>
        </label>
        <label htmlFor={`${id}-value`}>
          Value{unit}
          {control}
        </label>
      </div>
      <div className="acts">
        <button type="button" className="btn sm" onClick={() => onAdd('and')}>
          <Icon name="plus" />
          Add condition in series (AND)
        </button>
        <button type="button" className="btn sm" onClick={() => onAdd('or')}>
          <Icon name="plus" />
          Add parallel branch (OR)
        </button>
        <button type="button" className="btn sm" onClick={onRemove} disabled={!canRemove}>
          Remove
        </button>
      </div>
    </div>
  );
}
