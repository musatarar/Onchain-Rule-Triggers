import type { ConditionNode, GateTrace, MatchDetail, TokenRef } from '../api/types.ts';
import { type Comparison, type Describer, describeGate } from './describe.ts';
import { formatUnits, groupDigits, short } from './format.ts';
import { type GatePower, parentOf } from './power.ts';

/** What a gate read on this transaction, as drawn under it: "397.09", "Binance 15". */
export function observedText(gate: GateTrace | undefined): string {
  if (!gate) return '';
  if (gate.reason === 'no_transfer') return 'no token transfer';
  const seen = gate.observed;
  if (!seen) return '';
  switch (seen.kind) {
    case 'amount':
      return seen.decimals === null ? 'decimals unknown' : formatUnits(seen.raw, seen.decimals);
    case 'native_amount':
      return `${formatUnits(seen.wei, 18, 4)} ETH`;
    case 'address':
      return seen.address === null ? 'contract creation' : seen.label || short(seen.address);
    case 'token':
      return seen.token.symbol ?? short(seen.token.address);
    case 'bool':
      return seen.value ? 'recognised' : 'unrecognised';
    case 'method':
      return seen.signature ?? seen.selector ?? 'none';
  }
}

export type Insight = { reads: string; steps: string[]; test: string; note: string };

function listStep(node: Comparison, address: string | null, gate: GateTrace | undefined): string[] {
  if (node.operator !== 'in' || typeof node.value !== 'object' || !('addresses' in node.value)) return [];
  const list = node.value;
  const name = list.name ?? 'the list';
  const seen = gate?.observed?.kind === 'address' ? gate.observed : null;
  const hit = seen?.list_hit ?? (address !== null && list.addresses.includes(address));
  if (!hit) return [`not among the ${list.addresses.length} addresses on ${name}`];
  return [seen?.label ? `on ${name} as “${seen.label}”` : `on ${name}`];
}

/** How one gate read this transaction, step by step, for the inspector. */
export function explain(node: Comparison, gate: GateTrace | undefined, detail: MatchDetail, describer: Describer): Insight {
  const text = describeGate(node, describer);
  const test = (seen: string) => `${seen} ${text.test}`;
  const seen = gate?.observed;
  const transfer = detail.transfer;
  const tx = detail.transaction;

  if (node.source === 'token_transfer') {
    if (!transfer || gate?.reason === 'no_transfer') {
      return {
        reads: `token_transfer.${node.field}`,
        steps: ['No token transfer was decoded from this transaction'],
        test: 'nothing to compare',
        note: 'A token-transfer gate cannot hold on a transaction without a decoded transfer.',
      };
    }
    const token: TokenRef = seen?.kind === 'token' ? seen.token : transfer.token;
    if (node.field === 'amount') {
      const raw = seen?.kind === 'amount' ? seen.raw : transfer.raw_value;
      const decimals = seen?.kind === 'amount' ? seen.decimals : token.decimals;
      const steps = [`raw_value = ${groupDigits(raw)}`];
      if (decimals === null) {
        steps.push('token.decimals = unknown (not read from the contract yet)');
        return {
          reads: 'token_transfer.raw_value ÷ 10^token.decimals',
          steps,
          test: 'cannot scale the amount',
          note: 'Unknown decimals are never guessed, so this gate reports no data instead of holding or failing.',
        };
      }
      steps.push(
        `token.decimals = ${decimals}${token.symbol ? ` (${token.symbol})` : ''}`,
        `amount = ${formatUnits(raw, decimals, 6)}`,
      );
      return { reads: 'token_transfer.raw_value ÷ 10^token.decimals', steps, test: test(formatUnits(raw, decimals)), note: '' };
    }
    if (node.field === 'token') {
      return {
        reads: 'token_transfer.token → token catalog',
        steps: [
          `contract = ${token.address}`,
          token.symbol ? `catalog: ${token.name ?? token.symbol} (${token.symbol})` : 'catalog: no entry for this contract',
        ],
        test: test(token.symbol ?? short(token.address)),
        note: '',
      };
    }
    if (node.field === 'token_recognised') {
      return {
        reads: 'token catalog entry for token_transfer.token',
        steps: [
          `contract = ${token.address}`,
          token.symbol ? `found: ${token.name ?? token.symbol} (${token.symbol})` : 'not found in the catalog',
        ],
        test: test(token.symbol ? 'recognised' : 'unrecognised'),
        note: '',
      };
    }
    const address = seen?.kind === 'address' ? seen.address : node.field === 'from_address' ? transfer.from_address : transfer.to_address;
    const label = seen?.kind === 'address' ? seen.label : null;
    return {
      reads: `token_transfer.${node.field}`,
      steps: [`${node.field} = ${address}`, ...listStep(node, address, gate)],
      test: test(label || short(address)),
      note: '',
    };
  }

  if (node.field === 'value') {
    return {
      reads: 'transaction.value ÷ 10^18',
      steps: [`value = ${groupDigits(tx.value)} wei`, `= ${formatUnits(tx.value, 18, 4)} ETH`],
      test: test(`${formatUnits(tx.value, 18, 4)} ETH`),
      note: '',
    };
  }
  if (node.field === 'method') {
    return {
      reads: 'transaction.input selector → signature catalog',
      steps: [`selector = ${tx.input_selector ?? 'none (plain transfer)'}`, `signature = ${tx.method ?? 'not in the catalog'}`],
      test: test(tx.method ?? tx.input_selector ?? 'none'),
      note: '',
    };
  }
  const address = node.field === 'from_address' ? tx.from_address : tx.to_address;
  const label = seen?.kind === 'address' ? seen.label : null;
  return {
    reads: `transaction.${node.field}`,
    steps: [`${node.field} = ${address ?? 'none (contract creation)'}`, ...listStep(node, address, gate)],
    test: test(address ? label || short(address) : 'none'),
    note: '',
  };
}

export function powerText(gate: GatePower): string {
  if (!gate.pin) return 'No power reached this gate: an earlier step in series was blocked.';
  if (gate.pout) return 'Power arrived and passed through.';
  if (gate.held === null) return 'Power arrived but stopped here: there was no data to test.';
  return 'Power arrived and stopped here.';
}

/** "Step 2 of 3 in series (ALL)…" or "Branch 1 of 2 in parallel (ANY)…". */
export function wiringText(condition: ConditionNode, node: Comparison, trace: Record<number, GateTrace>): string {
  const parent = parentOf(condition, node);
  if (!parent) return '';
  const siblings = parent.children;
  const index = siblings.indexOf(node);
  const held = siblings.filter((child) => child.id !== null && trace[child.id]?.held === true).length;
  return parent.type === 'and'
    ? `Step ${index + 1} of ${siblings.length} in series (ALL). Power passes only if every step holds: ${held} of ${siblings.length} held.`
    : `Branch ${index + 1} of ${siblings.length} in parallel (ANY). One live branch is enough: ${held} of ${siblings.length} carried power.`;
}

export type GateTag = 'CARRIED POWER' | 'NO DATA' | 'BLOCKED';

export const gateTag = (gate: GatePower): GateTag =>
  gate.pout ? 'CARRIED POWER' : gate.held === null ? 'NO DATA' : 'BLOCKED';
