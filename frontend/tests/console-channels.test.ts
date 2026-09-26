/** The command line over the channel list: live filtering, and what Enter tunes to. */
import assert from 'node:assert/strict';
import { test } from 'node:test';

import circuits from '../src/console/api/demo/fixtures/circuits.json' with { type: 'json' };
import { channelMatches, showsAll, tuneTarget } from '../src/console/derive/channels.ts';

const rules = circuits as { id: number; tag: string; name: string; sentence: string; enabled: boolean }[];
const tags = (query: string) => channelMatches(rules, query).map((rule) => rule.tag);

test('"bnb" finds BNB-TOKENS, BNB-OUT and LINK-BNB, armed first', () => {
  assert.deepEqual(tags('bnb'), ['BNB-TOKENS', 'BNB-OUT', 'LINK-BNB']);
});

test('"pepe" puts PEPE-1B at the top, then circuits whose sentence says PEPE', () => {
  assert.deepEqual(tags('pepe'), ['PEPE-1B', 'BNB-OUT']);
  assert.equal(tuneTarget(rules, 'pepe'), 5);
});

test('Enter on "all" (or a prefix of it, or nothing) tunes to ALL', () => {
  for (const query of ['all', 'ALL', 'al', '', '  ']) {
    assert.equal(tuneTarget(rules, query), 'all', JSON.stringify(query));
    assert.equal(showsAll(query), true);
  }
  assert.equal(showsAll('pepe'), false);
});

test('a leading "tune", "ch" or "channel" is ignored, and every word must match', () => {
  assert.deepEqual(tags('tune pepe'), tags('pepe'));
  assert.deepEqual(tags('ch stable'), ['STABLE-2K']);
  assert.deepEqual(tags('channel binance link'), ['LINK-BNB']);
  assert.deepEqual(tags('unknown usdt'), []);
  assert.equal(tuneTarget(rules, 'zzz'), null);
});

test('an empty query lists every circuit: armed by id, then the disarmed ones', () => {
  assert.deepEqual(tags(''), ['STABLE-2K', 'BNB-TOKENS', 'ETH-10', 'UNKNOWN-TKN', 'PEPE-1B', 'ANY-1M', 'BNB-OUT', 'LINK-BNB']);
});
