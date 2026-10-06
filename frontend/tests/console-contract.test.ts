/**
 * The demo's propose and backtest answer in the contract's exact shapes, so switching
 * them to the real API changes where data comes from and nothing else. The compiler
 * already holds DemoConsoleApi to the types (it `implements DemoApi`); these check
 * what types cannot: uint256 values are decimal strings, unknown decimals are null
 * (never 18), addresses are lowercase, times are ISO, and every node has a trace.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { ApiError } from '../src/api/client.ts';
import { DemoConsoleApi } from '../src/console/api/demo/DemoConsoleApi.ts';
import type { ConditionNode, GateTrace, JournalRow, TokenRef } from '../src/console/api/types.ts';

const api = new DemoConsoleApi({ latency: [0, 0] });

const UINT = /^\d+$/;
const DECIMAL = /^\d+(\.\d+)?$/;
const ADDRESS = /^0x[0-9a-f]{40}$/;
const HASH = /^0x[0-9a-f]{64}$/;
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/;

function assertToken(token: TokenRef) {
  assert.match(token.address, ADDRESS);
  assert.ok(token.decimals === null || Number.isInteger(token.decimals), 'decimals is a number or null');
}

function assertRow(row: Omit<JournalRow, 'id' | 'rule'>) {
  assert.match(row.transaction.hash, HASH);
  assert.match(row.transaction.block_timestamp, ISO);
  assert.match(row.matched_at, ISO);
  const { headline } = row;
  assert.match(headline.from_address, ADDRESS);
  if (headline.to_address !== null) assert.match(headline.to_address, ADDRESS);
  assert.equal(typeof headline.amount.raw, 'string');
  assert.match(headline.amount.raw, UINT);
  if (headline.kind === 'native') {
    assert.equal(headline.token, null);
    assert.equal(headline.amount.decimals, 18);
  } else {
    assertToken(headline.token!);
    assert.equal(headline.amount.decimals, headline.token!.decimals);
    assert.equal(row.flags.decimals_unknown, headline.token!.decimals === null);
    assert.equal(row.flags.token_unrecognised, headline.token!.symbol === null);
  }
  if (headline.amount.decimals === null) assert.equal(headline.amount.value, null);
  else assert.match(headline.amount.value!, DECIMAL);
}

function assertGate(gate: GateTrace) {
  assert.ok(gate.held === true || gate.held === false || gate.held === null);
  const seen = gate.observed;
  if (!seen) return;
  if (seen.kind === 'amount') {
    assert.match(seen.raw, UINT);
    assert.ok(seen.decimals === null || Number.isInteger(seen.decimals));
    if (seen.decimals === null) {
      assert.equal(seen.value, null);
      assert.equal(gate.held, null, 'an amount with unknown decimals is no data, not a pass or a fail');
      assert.equal(gate.reason, 'decimals_unknown');
    }
  }
  if (seen.kind === 'native_amount') {
    assert.match(seen.wei, UINT);
    assert.match(seen.value, DECIMAL);
  }
  if (seen.kind === 'address' && seen.address !== null) assert.match(seen.address, ADDRESS);
  if (seen.kind === 'token') assertToken(seen.token);
}

function nodes(node: ConditionNode): ConditionNode[] {
  return node.type === 'comparison' ? [node] : [node, ...node.children.flatMap(nodes)];
}

function assertCondition(condition: ConditionNode) {
  assert.notEqual(condition.type, 'comparison', 'the root is a group');
  for (const node of nodes(condition)) {
    if (node.type !== 'comparison') continue;
    const value = node.value;
    assert.equal(node.source, 'token_transfer', 'every gate reads a token transfer');
    if (node.field === 'amount') assert.match(value as string, DECIMAL, 'amounts are decimal strings');
    if (node.field === 'token') assert.match((value as { address: string }).address, ADDRESS);
    if (node.operator === 'in') for (const address of (value as { addresses: string[] }).addresses) assert.match(address, ADDRESS);
  }
}

test('propose and backtest answer in contract shape, and not-understood is a 422', async () => {
  const proposal = await api.propose('PEPE or LINK above 1m');
  assertCondition(proposal.condition);
  for (const node of nodes(proposal.condition)) assert.equal(node.id, null, 'a proposal is unsaved: every id is null');

  await assert.rejects(api.propose('hello there'), (error: unknown) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 422);
    assert.equal(error.code, 'not_understood');
    return true;
  });

  let next = -1;
  const number = (node: ConditionNode): ConditionNode =>
    node.type === 'comparison' ? { ...node, id: next-- } : { ...node, id: next--, children: node.children.map(number) };
  const result = await api.backtest(number(proposal.condition));
  assert.equal(result.transactions_scanned, 613);
  assert.equal(result.match_count, result.matches.length);
  for (const row of result.matches) {
    assertRow(row);
    assert.ok(Object.keys(row.trace).every((key) => Number(key) < 0), 'traces are keyed by the ids the request sent');
    Object.values(row.trace).forEach(assertGate);
  }
});
