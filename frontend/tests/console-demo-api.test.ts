/**
 * The demo adapter behaves like the server the contract describes: it reproduces
 * the oracle computed from the same five blocks, orders and pages the journal the
 * contract's way, and recomputes matches when circuits change.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { DemoConsoleApi } from '../src/console/api/demo/DemoConsoleApi.ts';
import expected from '../src/console/api/demo/fixtures/expected-results.json' with { type: 'json' };
import type { JournalRow } from '../src/console/api/types.ts';

type Oracle = Record<string, { enabled: boolean; match_count: number; unevaluable_count: number; match_tx_hashes: string[] }>;
const oracle = expected as Oracle;

const demo = () => new DemoConsoleApi({ latency: [0, 0] });

async function allRows(api: DemoConsoleApi, rule?: number): Promise<JournalRow[]> {
  const rows: JournalRow[] = [];
  let cursor: string | undefined;
  do {
    const page = await api.matches({ rule, cursor, page_size: 7 });
    rows.push(...page.results);
    cursor = page.next ?? undefined;
  } while (cursor);
  return rows;
}

test('every demo circuit matches the oracle: counts, unevaluable counts and tx hashes', async () => {
  const api = demo();
  const rules = (await api.listRules()).results;
  assert.equal(rules.length, Object.keys(oracle).length);
  for (const rule of rules) {
    const want = oracle[rule.id];
    assert.equal(rule.enabled, want.enabled, `${rule.tag} enabled`);
    assert.equal(rule.stats.match_count, want.match_count, `${rule.tag} match_count`);
    assert.equal(rule.stats.unevaluable_count, want.unevaluable_count, `${rule.tag} unevaluable_count`);
    const hashes = (await allRows(api, rule.id)).map((row) => row.transaction.hash);
    assert.deepEqual(new Set(hashes), new Set(want.match_tx_hashes), `${rule.tag} hashes`);
    assert.equal(hashes.length, want.match_count, `${rule.tag} has no duplicate rows`);
  }
});

test('BNB-OUT has 7 matches, ANY-1M 3 and 9 unevaluable, and disarmed LINK-BNB none', async () => {
  const rules = (await demo().listRules()).results;
  const byTag = Object.fromEntries(rules.map((rule) => [rule.tag, rule]));
  assert.equal(byTag['BNB-OUT'].id, 8);
  assert.equal(byTag['BNB-OUT'].stats.match_count, 7);
  assert.equal(byTag['ANY-1M'].stats.match_count, 3);
  assert.equal(byTag['ANY-1M'].stats.unevaluable_count, 9);
  assert.equal(byTag['LINK-BNB'].enabled, false);
  assert.equal(byTag['LINK-BNB'].stats.match_count, 0);
});

test('the engine status counts armed circuits and their matches over the five blocks', async () => {
  const status = await demo().engineStatus();
  assert.deepEqual(status.rules, { total: 8, enabled: 7 });
  assert.equal(status.match_count, 40);
  assert.deepEqual(
    status.chains.map((c) => [c.chain, c.name, c.first_block, c.last_block]),
    [[1, 'Ethereum', 18_000_000, 18_000_004]],
  );
});

test('the journal is newest first: block desc, then tx index desc, then circuit id', async () => {
  const rows = await allRows(demo());
  assert.equal(rows.length, 40);
  for (let i = 1; i < rows.length; i++) {
    const a = rows[i - 1];
    const b = rows[i];
    const order =
      b.transaction.block_number - a.transaction.block_number ||
      b.transaction.transaction_index - a.transaction.transaction_index ||
      a.rule.id - b.rule.id;
    assert.ok(order < 0, `row ${i} is out of order`);
  }
});

test('journal pages chain through the cursor at 50 by default, and after=head is empty', async () => {
  const api = demo();
  const first = await api.matches({});
  assert.equal(first.results.length, 40);
  assert.equal(first.next, null);
  const newer = await api.matches({ after: first.head });
  assert.deepEqual(newer.results, []);
  const paged = await allRows(api);
  assert.deepEqual(
    paged.map((row) => row.id),
    first.results.map((row) => row.id),
  );
});

test('match ids are deterministic from the circuit and the transaction', async () => {
  const one = (await demo().matches({})).results.map((row) => `${row.id}:${row.rule.id}:${row.transaction.hash}`);
  const two = (await demo().matches({})).results.map((row) => `${row.id}:${row.rule.id}:${row.transaction.hash}`);
  assert.deepEqual(one, two);
});

test('disarming a circuit drops its matches and re-arming brings them back', async () => {
  const api = demo();
  await api.updateRule(8, { enabled: false });
  assert.equal((await api.matches({ rule: 8 })).results.length, 0);
  assert.equal((await api.engineStatus()).match_count, 33);
  const rearmed = await api.updateRule(8, { enabled: true });
  assert.equal(rearmed.stats.match_count, 7);
  assert.equal(rearmed.revision, 1, 'arming does not bump the revision');
});

test('a saved circuit is evaluated at once, and a condition change bumps its revision', async () => {
  const api = demo();
  const proposal = await api.propose('USDT transfers over 10k');
  const created = await api.createRule({
    name: proposal.name,
    tag: proposal.tag,
    glyph: proposal.glyph,
    sentence: 'USDT transfers over 10k',
    enabled: true,
    condition: proposal.condition,
  });
  assert.equal(created.tag, 'STABLE-10K');
  assert.equal(created.revision, 1);
  assert.equal(created.stats.match_count, 2);
  assert.ok(created.condition.id !== null && created.condition.id > 0, 'the server assigned ids');

  const renamed = await api.updateRule(created.id, { tag: 'USDT-10K', glyph: 'circle' });
  assert.equal(renamed.revision, 1, 'tag and glyph do not bump the revision');
  const widened = await api.updateRule(created.id, {
    condition: { ...created.condition, children: created.condition.type === 'comparison' ? [] : created.condition.children.slice(0, 1) },
  });
  assert.equal(widened.revision, 2);
  assert.ok(widened.stats.match_count > 2);

  await api.deleteRule(created.id);
  await assert.rejects(api.getRule(created.id), { status: 404 });
});

test('writes are validated like the server: tag format and uniqueness, name, condition', async () => {
  const api = demo();
  const base = (await api.getRule(1)).condition;
  await assert.rejects(api.updateRule(1, { tag: 'BNB-OUT' }), { status: 400, message: 'tag: BNB-OUT is already used by another circuit.' });
  await assert.rejects(api.updateRule(1, { tag: 'bad tag' }), { status: 400, message: /^tag: Tags use/ });
  await assert.rejects(api.updateRule(1, { name: '  ' }), { status: 400, message: /^name:/ });
  await assert.rejects(
    api.updateRule(1, { condition: { id: null, type: 'and', children: [{ id: null, type: 'comparison', source: 'token_transfer', field: 'amont', operator: 'gt', value: '1' }] } }),
    { status: 400, message: /^condition: .*has no field 'amont'/ },
  );
  assert.deepEqual((await api.getRule(1)).condition, base, 'a rejected write changes nothing');
});
