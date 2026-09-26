import type { ConsoleApi } from './ConsoleApi.ts';

export const SOURCE_KEYS = [
  'engineStatus',
  'vocabulary',
  'rules',
  'matches',
  'matchDetail',
  'tokens',
  'propose',
  'backtest',
] as const;

export type SourceKey = (typeof SOURCE_KEYS)[number];
export type Source = 'demo' | 'http';
export type Sources = Record<SourceKey, Source>;

/** Which source key decides each method; `rules` covers the list and CRUD. */
export const SOURCE_OF: Record<keyof ConsoleApi, SourceKey> = {
  engineStatus: 'engineStatus',
  vocabulary: 'vocabulary',
  listRules: 'rules',
  getRule: 'rules',
  createRule: 'rules',
  updateRule: 'rules',
  deleteRule: 'rules',
  matches: 'matches',
  matchDetail: 'matchDetail',
  tokens: 'tokens',
  propose: 'propose',
  backtest: 'backtest',
};

/**
 * Parses `VITE_CONSOLE_SOURCES`, e.g. "rules=http,matches=http". Unlisted keys are
 * `demo`, and an unset or blank value is all demo. Unknown keys and values throw, so
 * a typo fails the build's first page load instead of silently staying on demo.
 */
export function parseSources(raw: string | undefined): Sources {
  const sources = Object.fromEntries(SOURCE_KEYS.map((key) => [key, 'demo'])) as Sources;
  for (const entry of (raw ?? '').split(',')) {
    const pair = entry.trim();
    if (!pair) continue;
    const [key, value, ...rest] = pair.split('=').map((part) => part.trim());
    if (!(SOURCE_KEYS as readonly string[]).includes(key)) {
      throw new Error(`VITE_CONSOLE_SOURCES: unknown key "${key}". Known keys: ${SOURCE_KEYS.join(', ')}.`);
    }
    if (rest.length || (value !== 'demo' && value !== 'http')) {
      throw new Error(`VITE_CONSOLE_SOURCES: "${pair}" must be ${key}=demo or ${key}=http.`);
    }
    sources[key as SourceKey] = value;
  }
  return sources;
}
