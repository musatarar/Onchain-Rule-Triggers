/**
 * The demo backtest behaves like the server the contract describes: run on each
 * demo circuit, it reproduces the oracle computed from the same five blocks.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { DemoConsoleApi } from '../src/console/api/demo/DemoConsoleApi.ts';
import circuitsFixture from '../src/console/api/demo/fixtures/circuits.json' with { type: 'json' };
import expected from '../src/console/api/demo/fixtures/expected-results.json' with { type: 'json' };
import type { Rule } from '../src/console/api/types.ts';

type Oracle = Record<string, { enabled: boolean; match_count: number; unevaluable_count: number; match_tx_hashes: string[] }>;
const oracle = expected as Oracle;
const circuits = circuitsFixture as Omit<Rule, 'stats'>[];

const demo = () => new DemoConsoleApi({ latency: [0, 0] });

test('backtesting every armed demo circuit matches the oracle: counts, unevaluable counts and tx hashes', async () => {
  const api = demo();
  const armed = circuits.filter((circuit) => oracle[circuit.id].enabled);
  assert.equal(armed.length, 6);
  for (const circuit of armed) {
    const want = oracle[circuit.id];
    const result = await api.backtest(circuit.condition);
    assert.equal(result.match_count, want.match_count, `${circuit.tag} match_count`);
    assert.equal(result.unevaluable_count, want.unevaluable_count, `${circuit.tag} unevaluable_count`);
    const hashes = result.matches.map((row) => row.transaction.hash);
    assert.deepEqual(new Set(hashes), new Set(want.match_tx_hashes), `${circuit.tag} hashes`);
    assert.equal(hashes.length, want.match_count, `${circuit.tag} has no duplicate rows`);
  }
});

test('a backtest scans the five demo blocks', async () => {
  const result = await demo().backtest(circuits[0].condition);
  assert.deepEqual(result.window, { chain: 1, first_block: 18_000_000, last_block: 18_000_004 });
  assert.equal(result.transactions_scanned, 613);
});

test('a backtest is validated like the server, and returns at most 50 rows', async () => {
  const api = demo();
  await assert.rejects(
    api.backtest({ id: null, type: 'and', children: [{ id: null, type: 'comparison', source: 'token_transfer', field: 'amont', operator: 'gt', value: '1' }] }),
    { status: 400, message: /^condition: .*has no field 'amont'/ },
  );
  // A rule reads only token transfers, so a gate on a transaction is refused.
  await assert.rejects(
    api.backtest({ id: null, type: 'and', children: [{ id: null, type: 'comparison', source: 'transaction' as 'token_transfer', field: 'value', operator: 'gte', value: '0' }] }),
    { status: 400, message: /^condition: .*source: unknown source 'transaction'/ },
  );
  // No demo transfer is a burn, so this holds of each of the 68 transfers among the 613 transactions.
  const everything = await api.backtest({
    id: null,
    type: 'and',
    children: [{ id: null, type: 'comparison', source: 'token_transfer', field: 'to_address', operator: 'ne', value: `0x${'0'.repeat(40)}` }],
  });
  assert.equal(everything.match_count, 68);
  assert.equal(everything.matches.length, 50);
});
