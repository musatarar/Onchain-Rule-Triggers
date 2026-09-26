import type { ConditionNode, FieldType, Operator, Vocabulary } from '../api/types.ts';
import { groupDigits, short } from './format.ts';

export type Comparison = Extract<ConditionNode, { type: 'comparison' }>;
export type Group = Extract<ConditionNode, { type: 'and' | 'or' }>;

/** What describing a gate needs: the server's vocabulary, and token symbols by address. */
export type Describer = {
  vocabulary: Vocabulary | null;
  tokenSymbol: (chain: number, address: string) => string | null;
};

export type GateText = { subject: string; op: string; value: string; test: string };

// Without a vocabulary (still loading, or its source failing) a gate still reads
// sensibly: the type comes from the value's shape and the label from the key.
function inferredType(node: Comparison): FieldType {
  const value = node.value;
  if (typeof value === 'boolean') return 'bool';
  if (typeof value === 'object') return 'addresses' in value ? 'address' : 'token';
  if (/^0x[0-9a-f]{40}$/i.test(value)) return 'address';
  if (/^\d+(\.\d+)?$/.test(value)) return node.source === 'transaction' ? 'native_amount' : 'amount';
  return 'signature';
}

export function fieldOf(vocabulary: Vocabulary | null, node: Comparison): { label: string; type: FieldType } {
  const field = vocabulary?.sources.find((s) => s.key === node.source)?.fields.find((f) => f.key === node.field);
  return field ?? { label: node.field.replace(/_/g, ' '), type: inferredType(node) };
}

const OPERATOR_TEXT: Record<Operator, string> = {
  eq: 'is', ne: 'is not', in: 'is in', gt: '>', gte: '≥', lt: '<', lte: '≤',
};

export function operatorText(type: FieldType, operator: Operator): string {
  if ((type === 'amount' || type === 'native_amount') && operator === 'eq') return '=';
  return OPERATOR_TEXT[operator];
}

export function valueText(node: Comparison, describer: Describer): string {
  const { type } = fieldOf(describer.vocabulary, node);
  const value = node.value;
  if (type === 'address') {
    if (typeof value === 'object' && 'addresses' in value) {
      return value.name ?? `${value.addresses.length} addresses`;
    }
    return short(String(value));
  }
  if (type === 'amount') return groupDigits(String(value));
  if (type === 'native_amount') return `${groupDigits(String(value))} ETH`;
  if (type === 'bool') return value ? 'yes' : 'no';
  if (type === 'token' && typeof value === 'object' && 'address' in value) {
    return describer.tokenSymbol(value.chain, value.address) ?? short(value.address);
  }
  return String(value);
}

/** "Transfer amount" / "≥" / "250": the three lines a gate is drawn with. */
export function describeGate(node: Comparison, describer: Describer): GateText {
  if (node.field === 'token_recognised') {
    const value = node.value ? 'recognised' : 'unrecognised';
    const op = node.operator === 'ne' ? 'is not' : 'is';
    return { subject: 'Transfer token', op, value, test: `${op} ${value}` };
  }
  const { label, type } = fieldOf(describer.vocabulary, node);
  const subject = `${node.source === 'token_transfer' ? 'Transfer' : 'Tx'} ${label}`;
  const op = operatorText(type, node.operator);
  const value = valueText(node, describer);
  return { subject, op, value, test: `${op} ${value}` };
}

/** The whole gate in one line, as the inspector and the aria labels say it. */
export const gateSentence = (text: GateText): string => `${text.subject} ${text.test}`;
