import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DependencyList,
  type ReactNode,
} from 'react';
import { api, errorMessage } from './api/index.ts';
import type { ConditionNode, EngineStatus, Rule, TokenRef, Vocabulary } from './api/types.ts';
import type { Describer } from './derive/describe.ts';
import { leaves } from './derive/tree.ts';

export type Resource<T> = {
  status: 'loading' | 'ready' | 'error';
  data: T | null;
  error: string;
  reload: () => void;
};

/** Loads on mount and whenever `deps` change; a stale response never lands. */
export function useResource<T>(load: () => Promise<T>, deps: DependencyList): Resource<T> {
  const [state, setState] = useState<Omit<Resource<T>, 'reload'>>({ status: 'loading', data: null, error: '' });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setState((previous) => ({ ...previous, status: 'loading', error: '' }));
    load().then(
      (data) => live && setState({ status: 'ready', data, error: '' }),
      (error: unknown) => live && setState({ status: 'error', data: null, error: errorMessage(error) }),
    );
    return () => {
      live = false;
    };
    // `load` is a fresh closure every render; `deps` say when it means something new.
  }, [...deps, attempt]);
  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  return { ...state, reload };
}

const tokenKey = (chain: number, address: string) => `${chain}:${address.toLowerCase()}`;

/** Every token a condition compares against, so its symbol can be shown. */
export function tokenValues(conditions: ConditionNode[]): { chain: number; address: string }[] {
  return conditions
    .flatMap(leaves)
    .filter((node) => node.field === 'token' && typeof node.value === 'object' && 'address' in node.value)
    .map((node) => node.value as { chain: number; address: string })
    .filter((value) => /^0x[0-9a-f]{40}$/i.test(value.address));
}

/** Keys the journal sheet answers to; the shell routes them while the sheet is mounted. */
export type JournalKeys = {
  next(): void;
  prev(): void;
  channel(step: 1 | -1): void;
  replay(): void;
  focusSearch(): void;
};

type ConsoleState = {
  engine: Resource<EngineStatus>;
  rules: Resource<Rule[]>;
  vocabulary: Resource<Vocabulary>;
  describer: Describer;
  tokenOf(chain: number, address: string): TokenRef | null;
  /** Looks up any token symbols not yet known; failures leave the short address showing. */
  ensureTokens(values: { chain: number; address: string }[]): Promise<void>;
  learnTokens(tokens: TokenRef[]): void;
  /** Re-reads what a write changes: the circuits and the engine line. */
  refresh(): void;
  toast: { message: string; key: number } | null;
  showToast(message: string): void;
  selection: string;
  setSelection(text: string): void;
  journalSearch: string;
  setJournalSearch(search: string): void;
  journalKeys: { current: JournalKeys | null };
  /** The power-on animation plays on the session's first journal load, then only when asked. */
  playedOnce: { current: boolean };
};

const ConsoleContext = createContext<ConsoleState | null>(null);

export function useConsole(): ConsoleState {
  const state = useContext(ConsoleContext);
  if (!state) throw new Error('useConsole must be used inside <ConsoleProvider>');
  return state;
}

export function ConsoleProvider({ children }: { children: ReactNode }) {
  const [tokens, setTokens] = useState<Map<string, TokenRef | null>>(() => new Map());
  const known = useRef(tokens);
  known.current = tokens;
  const pending = useRef(new Map<string, Promise<void>>());

  const learnTokens = useCallback((found: TokenRef[]) => {
    setTokens((previous) => {
      const next = new Map(previous);
      for (const token of found) next.set(tokenKey(token.chain, token.address), token);
      return next;
    });
  }, []);

  const ensureTokens = useCallback(async (values: { chain: number; address: string }[]) => {
    const waits: Promise<void>[] = [];
    for (const { chain, address } of values) {
      const key = tokenKey(chain, address);
      if (known.current.has(key)) continue;
      let wait = pending.current.get(key);
      if (!wait) {
        const lower = address.toLowerCase();
        wait = api
          .tokens({ q: lower, chain })
          .then((page) => {
            const hit = page.results.find((token) => token.chain === chain && token.address === lower) ?? null;
            setTokens((previous) => new Map(previous).set(key, hit));
          })
          .catch(() => undefined)
          .finally(() => pending.current.delete(key));
        pending.current.set(key, wait);
      }
      waits.push(wait);
    }
    await Promise.all(waits);
  }, []);

  const engine = useResource(() => api.engineStatus(), []);
  const vocabulary = useResource(() => api.vocabulary(), []);
  const rules = useResource(async () => {
    const page = await api.listRules();
    await ensureTokens(tokenValues(page.results.map((rule) => rule.condition)));
    return page.results;
  }, []);

  const { reload: reloadEngine } = engine;
  const { reload: reloadRules } = rules;
  const refresh = useCallback(() => {
    reloadEngine();
    reloadRules();
  }, [reloadEngine, reloadRules]);

  const describer = useMemo<Describer>(
    () => ({
      vocabulary: vocabulary.data,
      tokenSymbol: (chain, address) => tokens.get(tokenKey(chain, address))?.symbol ?? null,
    }),
    [vocabulary.data, tokens],
  );

  const tokenOf = useCallback((chain: number, address: string) => tokens.get(tokenKey(chain, address)) ?? null, [tokens]);

  const [toast, setToast] = useState<ConsoleState['toast']>(null);
  const toastTimer = useRef<number | undefined>(undefined);
  const showToast = useCallback((message: string) => {
    window.clearTimeout(toastTimer.current);
    setToast((previous) => ({ message, key: (previous?.key ?? 0) + 1 }));
    toastTimer.current = window.setTimeout(() => setToast(null), 3800);
  }, []);
  useEffect(() => () => window.clearTimeout(toastTimer.current), []);

  const [selection, setSelection] = useState('—');
  const [journalSearch, setJournalSearch] = useState('');
  const journalKeys = useRef<JournalKeys | null>(null);
  const playedOnce = useRef(false);

  const value: ConsoleState = {
    engine,
    rules,
    vocabulary,
    describer,
    tokenOf,
    ensureTokens,
    learnTokens,
    refresh,
    toast,
    showToast,
    selection,
    setSelection,
    journalSearch,
    setJournalSearch,
    journalKeys,
    playedOnce,
  };
  return <ConsoleContext.Provider value={value}>{children}</ConsoleContext.Provider>;
}
