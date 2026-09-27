/** Circuit tags: the input's normalisation, the live error line, and the suggestion propose makes. */
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { DemoConsoleApi } from '../src/console/api/demo/DemoConsoleApi.ts';
import { suggestTag } from '../src/console/api/demo/parse.ts';
import type { ConditionNode } from '../src/console/api/types.ts';
import { normalizeTag, tagProblem } from '../src/console/derive/tags.ts';

const rules = [
  { id: 1, tag: 'STABLE-2K' },
  { id: 8, tag: 'BNB-OUT' },
];

test('the tag input uppercases, turns spaces into hyphens and stops at 12 characters', () => {
  assert.equal(normalizeTag('bnb out'), 'BNB-OUT');
  assert.equal(normalizeTag('stable  coins leaving'), 'STABLE-COINS');
});

test('a tag must be A–Z, 0–9 and hyphens, start with a letter or digit, and be unique', () => {
  assert.equal(tagProblem('', rules, null), 'Give the circuit a tag, e.g. BNB-OUT.');
  assert.equal(tagProblem('-OUT', rules, null), 'Tags use A–Z, 0–9 and hyphens, up to 12 characters.');
  assert.equal(tagProblem('BNB_OUT', rules, null), 'Tags use A–Z, 0–9 and hyphens, up to 12 characters.');
  assert.equal(tagProblem('ABCDEFGHIJKLM', rules, null), 'Tags use A–Z, 0–9 and hyphens, up to 12 characters.');
  assert.equal(tagProblem('BNB-OUT', rules, null), 'BNB-OUT is already used by another circuit.');
  assert.equal(tagProblem('BNB-OUT', rules, 8), '', 'a circuit keeps its own tag');
  assert.equal(tagProblem('BNB-OUT-2', rules, null), '');
});

test('propose suggests STABLE… for USDT + USDC and BNB-…-OUT for a from-list gate, 12 characters at most', async () => {
  const api = new DemoConsoleApi({ latency: [0, 0] });
  const stable = await api.propose('USDT or USDC transfers over 50k');
  assert.match(stable.tag, /^STABLE/);
  const leaving = await api.propose('Stablecoins leaving Binance over 300');
  assert.equal(leaving.tag, 'BNB-STAB-OUT');
  const eth = await api.propose('ETH over 20');
  assert.equal(eth.tag, 'ETH-20');
  for (const tag of [stable.tag, leaving.tag, eth.tag]) assert.ok(tag.length <= 12, tag);
  assert.equal(leaving.glyph, 'hexagon', 'the first glyph no circuit uses');
});

test('a suggested tag another circuit already has gets a counter', () => {
  const condition: ConditionNode = {
    id: null,
    type: 'and',
    children: [{ id: null, type: 'comparison', source: 'transaction', field: 'value', operator: 'gt', value: '10' }],
  };
  assert.equal(suggestTag(condition, [], () => null), 'ETH-10');
  assert.equal(suggestTag(condition, ['ETH-10'], () => null), 'ETH-10-2');
});
