/**
 * What the UI works out from `condition` + `trace` + `observed`: which gates carried
 * power, how a wide circuit folds, when each element lights, and how the inspector
 * explains a gate. The demo adapter supplies real match details to derive from.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { DemoConsoleApi } from '../src/console/api/demo/DemoConsoleApi.ts';
import vocabulary from '../src/console/api/demo/fixtures/vocabulary.json' with { type: 'json' };
import type { MatchDetail, TokenRef, Vocabulary } from '../src/console/api/types.ts';
import { type Comparison, describeGate, type Describer } from '../src/console/derive/describe.ts';
import { explain, gateTag, observedText, powerText, wiringText } from '../src/console/derive/explain.ts';
import { HOLD, layoutCircuit, SPEED } from '../src/console/derive/layout.ts';
import { powerPath } from '../src/console/derive/power.ts';

const api = new DemoConsoleApi({ latency: [0, 0] });
const BNB_OUT = 8;
const TX_3266 = '0x3266982fe13e591a4d52c05dac89063180dec8d089a6e7171f74d9edffca31fd';

const symbols = new Map<string, string | null>();
const describer: Describer = {
  vocabulary: vocabulary as Vocabulary,
  tokenSymbol: (_chain, address) => symbols.get(address) ?? null,
};
for (const token of (await api.tokens({ q: '' })).results as TokenRef[]) symbols.set(token.address, token.symbol);

async function matchOf(rule: number, predicate: (detail: MatchDetail) => boolean): Promise<MatchDetail> {
  for (const row of (await api.matches({ rule, page_size: 100 })).results) {
    const detail = await api.matchDetail(row.id);
    if (predicate(detail)) return detail;
  }
  throw new Error(`no match of rule ${rule} fits`);
}

const scene = (detail: Pick<MatchDetail, 'condition' | 'trace'> | { condition: MatchDetail['condition']; trace: null }, maxWidth: number) =>
  layoutCircuit({
    condition: detail.condition,
    trace: detail.trace,
    text: (node: Comparison) => describeGate(node, describer),
    value: (node: Comparison) => observedText(detail.trace?.[node.id!]),
    maxWidth,
    minWidth: maxWidth,
    compact: false,
  });

test('on BNB-OUT at 0x3266…31fd, G1, G2, G3 and G5 are live, G4 and G6–G8 stay dark, the coil is energised', async () => {
  const detail = await matchOf(BNB_OUT, (d) => d.transaction.hash === TX_3266);
  const power = powerPath(detail.condition, detail.trace);
  const live = power.gates.filter((g) => g.pout).map((g) => `G${g.number}`);
  const dark = power.gates.filter((g) => !g.pout).map((g) => `G${g.number}`);
  assert.deepEqual(live, ['G1', 'G2', 'G3', 'G5']);
  assert.deepEqual(dark, ['G4', 'G6', 'G7', 'G8']);
  assert.equal(power.energised, true);
  assert.equal(powerText(power.gates[6]), 'No power reached this gate: an earlier step in series was blocked.');
});

test('a gate with unknown decimals is no data: power arrives there and stops', async () => {
  // Beside a gate that holds, a no-data gate still lets its OR energise the coil.
  const created = await api.createRule({
    name: 'Big or recognised',
    tag: 'NODATA-T',
    glyph: 'circle',
    sentence: '',
    enabled: true,
    condition: {
      id: null,
      type: 'or',
      children: [
        { id: null, type: 'comparison', source: 'token_transfer', field: 'amount', operator: 'gt', value: '1000000' },
        { id: null, type: 'comparison', source: 'token_transfer', field: 'token_recognised', operator: 'eq', value: true },
      ],
    },
  });
  const detail = await matchOf(created.id, (d) => d.transfer?.token.symbol === 'CUBE');
  assert.equal(detail.transfer!.token.decimals, null);
  const power = powerPath(detail.condition, detail.trace);
  const [amount, recognised] = power.gates;
  assert.equal(amount.held, null);
  assert.equal(detail.trace[amount.node.id!].reason, 'decimals_unknown');
  assert.deepEqual([amount.pin, amount.pout], [true, false]);
  assert.equal(gateTag(amount), 'NO DATA');
  assert.equal(powerText(amount), 'Power arrived but stopped here: there was no data to test.');
  assert.equal(recognised.pout, true);
  assert.equal(power.energised, true);
  const drawn = scene(detail, 900);
  assert.equal(drawn.gates[0].state, 'unk');
  assert.equal(drawn.gates[0].value, 'decimals unknown');
  assert.equal(explain(amount.node, detail.trace[amount.node.id!], detail, describer).test, 'cannot scale the amount');
  await api.deleteRule(created.id);
});

test('an AND blocked early leaves its later steps without power', async () => {
  // BNB-OUT's PEPE branch at 0x3266…31fd: G6 (token is PEPE) blocks, so G7 never gets power.
  const detail = await matchOf(BNB_OUT, (d) => d.transaction.hash === TX_3266);
  const power = powerPath(detail.condition, detail.trace);
  const [g6, g7] = [power.gates[5], power.gates[6]];
  assert.deepEqual([g6.pin, g6.held, g6.pout], [true, false, false]);
  assert.deepEqual([g7.pin, g7.pout], [false, false]);
  assert.equal(powerText(g6), 'Power arrived and stopped here.');
  assert.equal(gateTag(g6), 'BLOCKED');
  assert.equal(
    wiringText(detail.condition, g6.node, detail.trace),
    'Step 1 of 2 in series (ALL). Power passes only if every step holds: 0 of 2 held.',
  );
});

test('at a narrow width BNB-OUT folds into two lines at its top-level AND, joined by marker A', async () => {
  const detail = await matchOf(BNB_OUT, (d) => d.transaction.hash === TX_3266);
  const wide = scene(detail, 1600);
  assert.equal(wide.lines, 1);
  assert.equal(wide.markers.length, 0);
  const narrow = scene(detail, 700);
  assert.equal(narrow.lines, 2);
  assert.deepEqual(
    narrow.markers.map((m) => m.letter),
    ['A', 'A'],
  );
  // The first OR (G1, G2) is on the first line; the second OR (G3…G8) and the coil on the next.
  const lineOf = (y: number) => (y < narrow.gates[2].y ? 1 : 2);
  assert.deepEqual(
    narrow.gates.map((g) => lineOf(g.y)),
    [1, 1, 2, 2, 2, 2, 2, 2],
  );
  assert.ok(narrow.coil.wy > narrow.gates[0].wy);
  assert.equal(narrow.width, 700, 'the right rail sits at the box edge');
});

test('a gate lights at x ÷ 820 plus 0.6 s for every live gate before it; the coil waits out all 4 holds', async () => {
  const detail = await matchOf(BNB_OUT, (d) => d.transaction.hash === TX_3266);
  const drawn = scene(detail, 1600);
  const live = drawn.gates.filter((g) => g.pout);
  assert.equal(live.length, 4);
  for (const gate of live) {
    const before = live.filter((other) => other.cx < gate.cx - 1).length;
    assert.ok(Math.abs(gate.delay - (gate.cx / SPEED + before * HOLD)) < 1e-9, `G${gate.number}`);
  }
  // G1 and G2 sit at the same x on parallel branches: they light together.
  assert.equal(live[0].delay, live[1].delay);
  assert.ok(Math.abs(drawn.coil.delay - ((drawn.coil.cx - 16) / SPEED + 4 * HOLD)) < 1e-9);
  // Folded, the second line's elements also wait for the first line's width.
  const folded = scene(detail, 700);
  assert.ok(folded.coil.delay > (folded.coil.cx - 16) / SPEED + 4 * HOLD);
});

test('the inspector explains a gate from what it observed', async () => {
  const detail = await matchOf(BNB_OUT, (d) => d.transaction.hash === TX_3266);
  const power = powerPath(detail.condition, detail.trace);
  const [g1, g2, g3, , g5] = power.gates;
  const read = (gate: typeof g1) => explain(gate.node, detail.trace[gate.node.id!], detail, describer);

  assert.deepEqual(read(g3), {
    reads: 'token_transfer.token → token catalog',
    steps: ['contract = 0xdac17f958d2ee523a2206206994597c13d831ec7', 'catalog: Tether (USDT)'],
    test: 'USDT is USDT',
    note: '',
  });
  assert.equal(wiringText(detail.condition, g3.node, detail.trace), 'Branch 1 of 2 in parallel (ANY). One live branch is enough: 1 of 2 carried power.');
  assert.deepEqual(read(g5).steps, ['raw_value = 397,092,712', 'token.decimals = 6 (USDT)', 'amount = 397.092712']);
  assert.equal(read(g5).test, '397.09 ≥ 250');
  assert.deepEqual(read(g2).steps, [
    'from_address = 0x21a31ee1afc51d94c2efccaa2092ad1028285549',
    'on Binance hot wallets as “Binance 15”',
  ]);
  assert.equal(read(g2).test, 'Binance 15 is in Binance hot wallets');
  assert.equal(wiringText(detail.condition, g1.node, detail.trace), 'Branch 1 of 2 in parallel (ANY). One live branch is enough: 2 of 2 carried power.');
  assert.equal(observedText(detail.trace[g5.node.id!]), '397.09');
  assert.equal(gateTag(g5), 'CARRIED POWER');
});
