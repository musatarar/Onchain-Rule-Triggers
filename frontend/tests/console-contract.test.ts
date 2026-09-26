/**
 * The demo adapter answers in the contract's exact shapes, so switching a source to
 * the real API changes where data comes from and nothing else. The compiler already
 * holds DemoConsoleApi to the types (it `implements ConsoleApi`); these check what
 * types cannot: uint256 values are decimal strings, unknown decimals are null (never
 * 18), addresses are lowercase, times are ISO, and every node has a trace.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { ApiError } from '../src/api/client.ts';
import { DemoConsoleApi } from '../src/console/api/demo/DemoConsoleApi.ts';
import type { ConditionNode, GateTrace, JournalRow, MatchDetail, TokenRef } from '../src/console/api/types.ts';

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
    if (node.field === 'amount' || node.field === 'value') assert.match(value as string, DECIMAL, 'amounts are decimal strings');
    if (node.field === 'token') assert.match((value as { address: string }).address, ADDRESS);
    if (node.operator === 'in') for (const address of (value as { addresses: string[] }).addresses) assert.match(address, ADDRESS);
  }
}

test('engine status, vocabulary and the rule list are contract-shaped', async () => {
  const status = await api.engineStatus();
  for (const chain of status.chains) assert.match(chain.last_block_at, ISO);
  const vocabulary = await api.vocabulary();
  assert.deepEqual(
    vocabulary.sources.map((s) => s.key),
    ['transaction', 'token_transfer'],
  );
  const page = await api.listRules();
  assert.equal(page.count, page.results.length);
  for (const rule of page.results) {
    assert.match(rule.tag, /^[A-Z0-9][A-Z0-9-]{0,11}$/);
    assert.match(rule.created_at, ISO);
    assertCondition(rule.condition);
    assert.ok(rule.stats.last_match_at === null || ISO.test(rule.stats.last_match_at));
  }
});

test('every journal row and match detail is contract-shaped, with a trace for every node', async () => {
  const rows = (await api.matches({ page_size: 100 })).results;
  assert.equal(rows.length, 40);
  for (const row of rows) {
    assertRow(row);
    const detail: MatchDetail = await api.matchDetail(row.id);
    assert.equal(detail.id, row.id);
    assertCondition(detail.condition);
    const trace = detail.trace;
    assert.ok(trace, 'the demo records a trace for every match');
    for (const node of nodes(detail.condition)) {
      assert.ok(node.id !== null && trace[node.id], `node ${node.id} has a trace`);
      assertGate(trace[node.id!]);
    }
    assert.match(detail.transaction.value, UINT);
    if (detail.transfer) {
      assert.match(detail.transfer.raw_value, UINT);
      assert.equal(detail.transfer.source, 'calldata');
      assert.equal(detail.transfer.verified, false, 'calldata transfers are never claimed as verified');
      assertToken(detail.transfer.token);
    }
    for (const other of detail.also_matched) assert.notEqual(other.match_id, detail.id);
  }
});

test('token search, propose and backtest answer in contract shape, and not-understood is a 422', async () => {
  const tokens = await api.tokens({ q: 'us', chain: 1 });
  assert.ok(tokens.results.some((token) => token.symbol === 'USDT'));
  tokens.results.forEach(assertToken);

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
  }
});
