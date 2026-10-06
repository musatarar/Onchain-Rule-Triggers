/**
 * What the UI works out from `condition` + `trace` + `observed`: which gates carried
 * power, how a wide circuit folds, when each element lights, and how the inspector
 * explains a gate. The demo backtest supplies real traces to derive from.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { DemoConsoleApi } from '../src/console/api/demo/DemoConsoleApi.ts';
import type { FixtureTransaction } from '../src/console/api/demo/evaluate.ts';
import circuits from '../src/console/api/demo/fixtures/circuits.json' with { type: 'json' };
import tokens from '../src/console/api/demo/fixtures/tokens.json' with { type: 'json' };
import transactions from '../src/console/api/demo/fixtures/transactions.json' with { type: 'json' };
import vocabulary from '../src/console/api/demo/fixtures/vocabulary.json' with { type: 'json' };
import type { ConditionNode, MatchDetail, Rule, TokenRef, Vocabulary } from '../src/console/api/types.ts';
import { type Comparison, describeGate, type Describer } from '../src/console/derive/describe.ts';
import { explain, gateTag, observedText, powerText, wiringText } from '../src/console/derive/explain.ts';
import { short } from '../src/console/derive/format.ts';
import { HOLD, layoutCircuit, SPEED } from '../src/console/derive/layout.ts';
import { powerPath } from '../src/console/derive/power.ts';

const api = new DemoConsoleApi({ latency: [0, 0] });
const BNB_OUT = (circuits as Omit<Rule, 'stats'>[]).find((circuit) => circuit.tag === 'BNB-OUT')!.condition;
const TX_3266 = '0x3266982fe13e591a4d52c05dac89063180dec8d089a6e7171f74d9edffca31fd';

const symbols = new Map<string, string | null>();
const describer: Describer = {
  vocabulary: vocabulary as Vocabulary,
  tokenSymbol: (_chain, address) => symbols.get(address) ?? null,
};
for (const token of tokens as TokenRef[]) symbols.set(token.address, token.symbol);

/** A match detail as the server would serve it, from a backtest row of `condition` and its transaction. */
function detailOf(condition: ConditionNode, row: Awaited<ReturnType<typeof api.backtest>>['matches'][number]): MatchDetail {
  const tx = (transactions as unknown as FixtureTransaction[]).find((candidate) => candidate.hash === row.transaction.hash)!;
  return {
    ...row,
    id: 0,
    rule: { id: 0, name: '', tag: '', glyph: 'circle' },
    condition,
    transaction: {
      ...row.transaction,
      from_address: tx.from_address,
      to_address: tx.to_address,
      value: tx.value,
      input_selector: tx.input_selector,
      method: tx.method,
      decode_status: tx.decode_status,
    },
    // A backtest row is a transfer the tree held of, so its transaction made one.
    transfer: {
      token: row.headline.token,
      from_address: tx.transfer!.from_address,
      to_address: tx.transfer!.to_address,
      raw_value: tx.transfer!.raw_value,
      log_index: tx.transfer!.log_index,
      source: tx.transfer!.source,
      verified: tx.transfer!.verified,
    },
    also_matched: [],
  };
}

async function matchOf(condition: ConditionNode, predicate: (detail: MatchDetail) => boolean): Promise<MatchDetail> {
  for (const row of (await api.backtest(condition)).matches) {
    const detail = detailOf(condition, row);
    if (predicate(detail)) return detail;
  }
  throw new Error('no backtest match fits');
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

test('on BNB-OUT at 0x3266…31fd, G1, G2 and G4 are live, G3, G5 and G6 stay dark, the coil is energised', async () => {
  const detail = await matchOf(BNB_OUT, (d) => d.transaction.hash === TX_3266);
  const power = powerPath(detail.condition, detail.trace);
  const live = power.gates.filter((g) => g.pout).map((g) => `G${g.number}`);
  const dark = power.gates.filter((g) => !g.pout).map((g) => `G${g.number}`);
  assert.deepEqual(live, ['G1', 'G2', 'G4']);
  assert.deepEqual(dark, ['G3', 'G5', 'G6']);
  assert.equal(power.energised, true);
  assert.equal(powerText(power.gates[5]), 'No power reached this gate: an earlier step in series was blocked.');
});

test('a gate with unknown decimals is no data: power arrives there and stops', async () => {
  // Beside a gate that holds, a no-data gate still lets its OR energise the coil.
  const condition: ConditionNode = {
    id: 1,
    type: 'or',
    children: [
      { id: 2, type: 'comparison', source: 'token_transfer', field: 'amount', operator: 'gt', value: '1000000' },
      { id: 3, type: 'comparison', source: 'token_transfer', field: 'token_recognised', operator: 'eq', value: true },
    ],
  };
  const detail = await matchOf(condition, (d) => d.transfer.token.symbol === 'CUBE');
  assert.equal(detail.transfer.token.decimals, null);
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
});

test('an AND blocked early leaves its later steps without power', async () => {
  // BNB-OUT's PEPE branch at 0x3266…31fd: G5 (token is PEPE) blocks, so G6 never gets power.
  const detail = await matchOf(BNB_OUT, (d) => d.transaction.hash === TX_3266);
  const power = powerPath(detail.condition, detail.trace);
  const [g5, g6] = [power.gates[4], power.gates[5]];
  assert.deepEqual([g5.pin, g5.held, g5.pout], [true, false, false]);
  assert.deepEqual([g6.pin, g6.pout], [false, false]);
  assert.equal(powerText(g5), 'Power arrived and stopped here.');
  assert.equal(gateTag(g5), 'BLOCKED');
  assert.equal(
    wiringText(detail.condition, g5.node, detail.trace),
    'Step 1 of 2 in series (ALL). Power passes only if every step holds: 0 of 2 held.',
  );
});

test('at a narrow width BNB-OUT folds into two lines at its top-level AND, joined by marker A', async () => {
  const detail = await matchOf(BNB_OUT, (d) => d.transaction.hash === TX_3266);
  const wide = scene(detail, 1600);
  assert.equal(wide.lines, 1);
  assert.equal(wide.markers.length, 0);
  const narrow = scene(detail, 600);
  assert.equal(narrow.lines, 2);
  assert.deepEqual(
    narrow.markers.map((m) => m.letter),
    ['A', 'A'],
  );
  // The from-list gate (G1) is on the first line; the OR (G2…G6) and the coil on the next.
  const lineOf = (y: number) => (y < narrow.gates[1].y ? 1 : 2);
  assert.deepEqual(
    narrow.gates.map((g) => lineOf(g.y)),
    [1, 2, 2, 2, 2, 2],
  );
  assert.ok(narrow.coil.wy > narrow.gates[0].wy);
  assert.equal(narrow.width, 600, 'the right rail sits at the box edge');
});

test('a gate lights at x ÷ 820 plus 0.6 s for every live gate before it; the coil waits out all 3 holds', async () => {
  const detail = await matchOf(BNB_OUT, (d) => d.transaction.hash === TX_3266);
  const drawn = scene(detail, 1600);
  const live = drawn.gates.filter((g) => g.pout);
  assert.equal(live.length, 3);
  for (const gate of live) {
    const before = live.filter((other) => other.cx < gate.cx - 1).length;
    assert.ok(Math.abs(gate.delay - (gate.cx / SPEED + before * HOLD)) < 1e-9, `G${gate.number}`);
  }
  // G1 is in series before the OR: G2 lights only after G1's hold.
  assert.ok(live[1].delay > live[0].delay + HOLD);
  assert.ok(Math.abs(drawn.coil.delay - ((drawn.coil.cx - 16) / SPEED + 3 * HOLD)) < 1e-9);
  // Folded, the second line's elements also wait for the first line's width.
  const folded = scene(detail, 600);
  assert.ok(folded.coil.delay > (folded.coil.cx - 16) / SPEED + 3 * HOLD);
});

test('the inspector explains a gate from what it observed', async () => {
  const detail = await matchOf(BNB_OUT, (d) => d.transaction.hash === TX_3266);
  const power = powerPath(detail.condition, detail.trace);
  const [g1, g2, , g4] = power.gates;
  const read = (gate: typeof g1) => explain(gate.node, detail.trace[gate.node.id!], detail, describer);

  assert.deepEqual(read(g2), {
    reads: 'token_transfer.token → token catalog',
    steps: ['contract = 0xdac17f958d2ee523a2206206994597c13d831ec7', 'catalog: Tether (USDT)'],
    test: 'USDT is USDT',
    note: '',
  });
  assert.equal(wiringText(detail.condition, g2.node, detail.trace), 'Branch 1 of 2 in parallel (ANY). One live branch is enough: 1 of 2 carried power.');
  assert.deepEqual(read(g4).steps, ['raw_value = 397,092,712', 'token.decimals = 6 (USDT)', 'amount = 397.092712']);
  assert.equal(read(g4).test, '397.09 ≥ 250');
  assert.deepEqual(read(g1).steps, [
    'from_address = 0x21a31ee1afc51d94c2efccaa2092ad1028285549',
    'on Binance hot wallets as “Binance 15”',
  ]);
  assert.equal(read(g1).test, 'Binance 15 is in Binance hot wallets');
  assert.equal(wiringText(detail.condition, g1.node, detail.trace), 'Step 1 of 2 in series (ALL). Power passes only if every step holds: 2 of 2 held.');
  assert.equal(observedText(detail.trace[g4.node.id!]), '397.09');
  assert.equal(gateTag(g4), 'CARRIED POWER');
});

test('a match detail as the server serves it draws the circuit and explains each gate from observed', () => {
  // The shape `GET /api/matches/<id>/` answers: no address labels, and a token ref
  // filled in from the catalog when the match is read.
  const usdt = '0xdac17f958d2ee523a2206206994597c13d831ec7';
  const recipient = '0x5ac1a1d4a6d5b3a7d9e0f4f2c8b1e3a5d7c9b047';
  const detail = {
    condition: {
      id: 41,
      type: 'and',
      children: [
        { id: 42, type: 'comparison', source: 'token_transfer', field: 'token', operator: 'eq', value: { chain: 1, address: usdt } },
        { id: 43, type: 'comparison', source: 'token_transfer', field: 'amount', operator: 'gte', value: '250' },
        { id: 44, type: 'comparison', source: 'token_transfer', field: 'to_address', operator: 'eq', value: recipient },
      ],
    },
    trace: {
      41: { held: true },
      42: {
        held: true,
        observed: { kind: 'token', token: { chain: 1, address: usdt, symbol: 'USDT', name: 'Tether', decimals: 6 } },
      },
      43: { held: true, observed: { kind: 'amount', raw: '397092712', decimals: 6, value: '397.092712' } },
      44: { held: true, observed: { kind: 'address', address: recipient, list_hit: null } },
    },
    transaction: { value: '0', input_selector: '0xa9059cbb', method: 'transfer' },
    transfer: {
      token: { chain: 1, address: usdt, symbol: 'USDT', name: 'Tether', decimals: 6 },
      to_address: recipient,
      raw_value: '397092712',
    },
  } as unknown as MatchDetail;

  const power = powerPath(detail.condition, detail.trace);
  assert.equal(power.energised, true);
  assert.deepEqual(
    power.gates.map((g) => g.pout),
    [true, true, true],
  );
  const drawn = scene(detail, 1600);
  assert.equal(drawn.coil.lit, true);
  // Lit gates and the coil carry the delays the power-on animation, and Replay, play.
  assert.ok(drawn.gates.every((g) => g.pout && g.delay > 0));
  assert.ok(drawn.coil.delay > Math.max(...drawn.gates.map((g) => g.delay)));
  assert.deepEqual(
    drawn.gates.map((g) => g.value),
    ['USDT', '397.09', short(recipient)],
  );

  const [byToken, amount, to] = power.gates;
  const read = (gate: typeof byToken) => explain(gate.node, detail.trace![gate.node.id!], detail, describer);
  assert.deepEqual(read(byToken).steps, [`contract = ${usdt}`, 'catalog: Tether (USDT)']);
  assert.deepEqual(read(amount).steps, ['raw_value = 397,092,712', 'token.decimals = 6 (USDT)', 'amount = 397.092712']);
  assert.deepEqual(read(to).steps, [`to_address = ${recipient}`]);
  assert.equal(read(to).test, `${short(recipient)} is ${short(recipient)}`);
});
