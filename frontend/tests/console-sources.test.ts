/** VITE_CONSOLE_SOURCES: parsing, validation, and each method going to its source. */
import assert from 'node:assert/strict';
import { test } from 'node:test';

import type { ConsoleApi } from '../src/console/api/ConsoleApi.ts';
import { routeApi } from '../src/console/api/index.ts';
import { parseSources, SOURCE_KEYS, SOURCE_OF } from '../src/console/api/sources.ts';

test('unset or blank means every source is demo', () => {
  for (const raw of [undefined, '', '  ', ',']) {
    assert.deepEqual(Object.values(parseSources(raw)), SOURCE_KEYS.map(() => 'demo'), JSON.stringify(raw));
  }
});

test('listed keys switch to http and the rest stay demo', () => {
  const sources = parseSources(' rules=http , matches=http,backtest=demo ');
  assert.equal(sources.rules, 'http');
  assert.equal(sources.matches, 'http');
  assert.equal(sources.backtest, 'demo');
  assert.equal(sources.engineStatus, 'demo');
});

test('an unknown key or value throws instead of silently staying on demo', () => {
  assert.throws(() => parseSources('rule=http'), /unknown key "rule"/);
  assert.throws(() => parseSources('rules=live'), /must be rules=demo or rules=http/);
  assert.throws(() => parseSources('rules'), /must be rules=demo or rules=http/);
  assert.throws(() => parseSources('rules=http=demo'), /must be/);
});

test('every ConsoleApi method has a source key, and routing follows it', async () => {
  const methods = Object.keys(SOURCE_OF) as (keyof ConsoleApi)[];
  const fake = (name: string) =>
    Object.fromEntries(methods.map((method) => [method, async () => `${name}:${method}`])) as unknown as ConsoleApi;
  const api = routeApi(parseSources('engineStatus=http,rules=http'), fake('demo'), fake('http'));
  assert.equal(await api.engineStatus(), 'http:engineStatus');
  assert.equal(await api.vocabulary(), 'demo:vocabulary');
  for (const method of ['listRules', 'getRule', 'createRule', 'updateRule', 'deleteRule'] as const) {
    assert.equal(await (api[method] as () => Promise<unknown>)(), `http:${method}`);
  }
  assert.equal(await api.matches({}), 'demo:matches');
  assert.equal(await api.backtest({ id: null, type: 'and', children: [] }), 'demo:backtest');
  assert.deepEqual(new Set(Object.values(SOURCE_OF)), new Set(SOURCE_KEYS));
});
