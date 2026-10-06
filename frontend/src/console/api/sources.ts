import type { ConsoleApi } from './ConsoleApi.ts';

/** The methods the server has no endpoint for yet, which can still run on demo data. */
export const SOURCE_KEYS = ['propose', 'backtest'] as const;

export type SourceKey = (typeof SOURCE_KEYS)[number];
export type Source = 'demo' | 'http';
export type Sources = Record<SourceKey, Source>;
export type DemoApi = Pick<ConsoleApi, SourceKey>;

/**
 * Parses `VITE_CONSOLE_SOURCES`, e.g. "propose=http". Unlisted keys are `demo`, and
 * an unset or blank value is all demo. Unknown keys and values throw, so a typo, or
 * a key for an endpoint that is always on the real API, fails the build's first
 * page load instead of being silently ignored.
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
