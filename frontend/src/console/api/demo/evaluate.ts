import type { ConditionNode, GateTrace, TokenRef } from '../types.ts';
import { compareScaled, scaled } from './decimal.ts';

/** A row of fixtures/transactions.json: the ingested transaction plus its decoded transfer. */
export type FixtureTransaction = {
  chain: number;
  hash: string;
  block_number: number;
  transaction_index: number;
  block_timestamp: string;
  from_address: string;
  to_address: string | null;
  value: string;
  input_selector: string | null;
  method: string | null;
  decode_status: 'INGESTED' | 'PROCESSING' | 'DECODED' | 'UNABLE_TO_DECODE';
  transfer: null | {
    token_address: string;
    from_address: string;
    to_address: string;
    raw_value: string;
    log_index: number | null;
    source: 'calldata' | 'log';
    verified: boolean;
    method: string;
  };
};

export type Lookup = {
  token(chain: number, address: string): TokenRef;
  label(address: string): string | null;
};

type Comparison = Extract<ConditionNode, { type: 'comparison' }>;

function ordered(operator: Comparison['operator'], order: number): boolean {
  switch (operator) {
    case 'gt': return order > 0;
    case 'gte': return order >= 0;
    case 'lt': return order < 0;
    case 'lte': return order <= 0;
    case 'eq': return order === 0;
    case 'ne': return order !== 0;
    default: return false;
  }
}

function equality(operator: Comparison['operator'], same: boolean): boolean {
  return operator === 'eq' ? same : operator === 'ne' ? !same : false;
}

function addressGate(node: Comparison, address: string | null, lookup: Lookup): GateTrace {
  const label = address ? lookup.label(address) : null;
  if (node.operator === 'in') {
    const list = node.value as { addresses: string[] };
    const hit = address !== null && list.addresses.some((entry) => entry.toLowerCase() === address);
    return { held: hit, observed: { kind: 'address', address, list_hit: hit, label } };
  }
  const held = equality(node.operator, address === String(node.value).toLowerCase());
  return { held, observed: { kind: 'address', address, list_hit: null, label } };
}

/** One gate against one transaction: whether it held, and what it read. */
export function evaluateComparison(node: Comparison, tx: FixtureTransaction, lookup: Lookup): GateTrace {
  if (node.source === 'token_transfer') {
    const transfer = tx.transfer;
    if (!transfer) return { held: false, reason: 'no_transfer' };
    const token = lookup.token(tx.chain, transfer.token_address);
    switch (node.field) {
      case 'token': {
        const want = node.value as { chain: number; address: string };
        const same = want.chain === token.chain && want.address.toLowerCase() === token.address;
        return { held: equality(node.operator, same), observed: { kind: 'token', token } };
      }
      case 'amount': {
        const raw = transfer.raw_value;
        if (token.decimals === null) {
          return {
            held: null,
            reason: 'decimals_unknown',
            observed: { kind: 'amount', raw, decimals: null, value: null },
          };
        }
        return {
          held: ordered(node.operator, compareScaled(raw, token.decimals, String(node.value))),
          observed: { kind: 'amount', raw, decimals: token.decimals, value: scaled(raw, token.decimals) },
        };
      }
      case 'from_address':
        return addressGate(node, transfer.from_address, lookup);
      case 'to_address':
        return addressGate(node, transfer.to_address, lookup);
      case 'token_recognised': {
        const recognised = token.symbol !== null;
        return { held: equality(node.operator, recognised === node.value), observed: { kind: 'bool', value: recognised } };
      }
    }
  } else {
    switch (node.field) {
      case 'value':
        return {
          held: ordered(node.operator, compareScaled(tx.value, 18, String(node.value))),
          observed: { kind: 'native_amount', wei: tx.value, value: scaled(tx.value, 18) },
        };
      case 'from_address':
        return addressGate(node, tx.from_address, lookup);
      case 'to_address': {
        const gate = addressGate(node, tx.to_address, lookup);
        return tx.to_address === null && gate.held === false ? { ...gate, reason: 'no_to_address' } : gate;
      }
      case 'method': {
        const actual = tx.method ?? tx.input_selector ?? 'none';
        return {
          held: equality(node.operator, actual === String(node.value)),
          observed: { kind: 'method', selector: tx.input_selector, signature: tx.method },
        };
      }
    }
  }
  throw new Error(`No field ${node.source}.${node.field}`);
}

/**
 * Evaluates the whole tree without short-circuiting, recording a GateTrace for every
 * node that has an id. AND and OR are three-valued: null (no data) sits between.
 */
export function evaluate(
  node: ConditionNode,
  tx: FixtureTransaction,
  lookup: Lookup,
  trace: Record<number, GateTrace>,
): boolean | null {
  let result: GateTrace;
  if (node.type === 'comparison') {
    result = evaluateComparison(node, tx, lookup);
  } else {
    const kids = node.children.map((child) => evaluate(child, tx, lookup, trace));
    const held =
      node.type === 'and'
        ? kids.includes(false) ? false : kids.includes(null) ? null : true
        : kids.includes(true) ? true : kids.includes(null) ? null : false;
    result = { held };
  }
  if (node.id !== null) trace[node.id] = result;
  return result.held;
}
