/** VITE_CONSOLE_SOURCES: parsing, validation, and each method going to its source. */
import assert from 'node:assert/strict';
import { test } from 'node:test';

import type { ConsoleApi } from '../src/console/api/ConsoleApi.ts';
import { routeApi } from '../src/console/api/index.ts';
import { type DemoApi, parseSources, SOURCE_KEYS } from '../src/console/api/sources.ts';

test('unset or blank means propose and backtest are demo', () => {
  for (const raw of [undefined, '', '  ', ',']) {
    assert.deepEqual(parseSources(raw), { propose: 'demo', backtest: 'demo' }, JSON.stringify(raw));
  }
});

test('listed keys switch to http and the rest stay demo', () => {
  assert.deepEqual(parseSources(' propose=http '), { propose: 'http', backtest: 'demo' });
  assert.deepEqual(parseSources('propose=demo,backtest=http'), { propose: 'demo', backtest: 'http' });
});

test('an unknown key or value throws instead of being silently ignored', () => {
  assert.throws(() => parseSources('proposal=http'), /unknown key "proposal"/);
  // Every other endpoint is always on the real API, so its old key is an error too.
  assert.throws(() => parseSources('rules=http'), /unknown key "rules". Known keys: propose, backtest/);
  assert.throws(() => parseSources('propose=live'), /must be propose=demo or propose=http/);
  assert.throws(() => parseSources('propose'), /must be propose=demo or propose=http/);
  assert.throws(() => parseSources('propose=http=demo'), /must be/);
});

test('every method but propose and backtest goes to http, and those two follow their source', async () => {
  const fake = (name: string, methods: string[]) =>
    Object.fromEntries(methods.map((method) => [method, async () => `${name}:${method}`]));
  const all = [
    'engineStatus', 'vocabulary', 'listRules', 'getRule', 'createRule', 'updateRule', 'deleteRule',
    'matches', 'matchDetail', 'tokens', 'propose', 'backtest',
  ];
  const demo = fake('demo', [...SOURCE_KEYS]) as unknown as DemoApi;
  const http = fake('http', all) as unknown as ConsoleApi;
  const api = routeApi(parseSources('backtest=http'), demo, http);
  for (const method of all.filter((name) => name !== 'propose')) {
    assert.equal(await (api[method as keyof ConsoleApi] as () => Promise<unknown>)(), `http:${method}`);
  }
  assert.equal(await api.propose(''), 'demo:propose');
});
