import type { ConditionNode, Glyph, Operator, TokenRef } from '../types.ts';
import { shiftDecimal } from './decimal.ts';

/** What the stand-in parser knows: the token catalog and the named address lists in use. */
export type ParserCatalog = {
  chain: number;
  tokens: TokenRef[];
  lists: { addresses: string[]; name?: string }[];
};

export type Parsed = { condition: ConditionNode | null; name: string; understood: string[] };

type Comparison = Extract<ConditionNode, { type: 'comparison' }>;
type Group = Extract<ConditionNode, { type: 'and' | 'or' }>;

const C = (
  source: Comparison['source'],
  field: string,
  operator: Operator,
  value: Comparison['value'],
): Comparison => ({ id: null, type: 'comparison', source, field, operator, value });

const G = (type: Group['type'], ...children: ConditionNode[]): Group => ({ id: null, type, children });

const short = (address: string) => `${address.slice(0, 6)}…${address.slice(-4)}`;

/** Flattens a group into a same-type parent and unwraps single-child groups below the root. */
export function normalize(node: ConditionNode): ConditionNode {
  if (node.type === 'comparison') return node;
  const children = node.children
    .map(normalize)
    .flatMap((child) => (child.type === node.type ? (child as Group).children : [child]))
    .map((child) => (child.type !== 'comparison' && child.children.length === 1 ? child.children[0] : child));
  return { ...node, children };
}

export function leaves(node: ConditionNode): Comparison[] {
  return node.type === 'comparison' ? [node] : node.children.flatMap(leaves);
}

const POWER: Record<string, number> = { k: 3, thousand: 3, m: 6, mm: 6, million: 6, b: 9, bn: 9, billion: 9 };
const SYMBOL_OPERATORS: Record<string, Operator> = { '>': 'gt', '>=': 'gte', '<': 'lt', '<=': 'lte' };
const WORD_OPERATORS: [RegExp, Operator][] = [
  [/\b(over|above|more than|greater than|exceeding)\s*$/, 'gt'],
  [/\b(at least|no less than)\s*$/, 'gte'],
  [/\b(under|below|less than)\s*$/, 'lt'],
  [/\b(at most|no more than)\s*$/, 'lte'],
];
const AMOUNT_RE =
  /(over|above|more than|greater than|exceeding|at least|no less than|under|below|less than|at most|no more than|>=|<=|>|<)\s*\$?\s*([\d][\d,]*(?:\.\d+)?)\s*(k|mm|m|bn|b|thousand|million|billion)?\b/;
const escapeRe = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** Turns a sentence into a Condition tree, the way the prototype's parser does. */
export function parseSentence(sentence: string, catalog: ParserCatalog): Parsed {
  const s = sentence.trim();
  const lower = s.toLowerCase();
  const parts: ConditionNode[] = [];
  const understood: string[] = [];
  const bySymbol = new Map<string, TokenRef>();
  for (const token of catalog.tokens) {
    if (token.symbol && !bySymbol.has(token.symbol)) bySymbol.set(token.symbol, token);
  }
  const tokenValue = (symbol: string) => ({ chain: catalog.chain, address: bySymbol.get(symbol)!.address });

  const found: string[] = [];
  for (const symbol of bySymbol.keys()) {
    const re = new RegExp(`\\b${escapeRe(symbol)}\\b`, symbol.length <= 2 ? '' : 'i');
    if (re.test(s) && !found.includes(symbol)) found.push(symbol);
  }
  if (/\b(stablecoins?|stables)\b/i.test(s)) {
    for (const symbol of ['USDT', 'USDC']) if (bySymbol.has(symbol) && !found.includes(symbol)) found.push(symbol);
  }
  const isEth = /\b(eth|ether)\b/i.test(s) && !found.length;
  if (/\b(unrecogni[sz]ed|unknown)\b/i.test(s)) {
    parts.push(C('token_transfer', 'token_recognised', 'eq', false));
    understood.push('unrecognised token');
  }
  if (found.length === 1) {
    parts.push(C('token_transfer', 'token', 'eq', tokenValue(found[0])));
    understood.push(found[0]);
  } else if (found.length > 1) {
    parts.push(G('or', ...found.map((symbol) => C('token_transfer', 'token', 'eq', tokenValue(symbol)))));
    understood.push(found.join(' or '));
  }

  const amount = lower.match(AMOUNT_RE);
  if (amount) {
    const value = shiftDecimal(amount[2].replace(/,/g, ''), POWER[amount[3] ?? ''] ?? 0);
    const operator =
      SYMBOL_OPERATORS[amount[1]] ?? WORD_OPERATORS.find(([re]) => re.test(amount[1]))?.[1] ?? 'gt';
    parts.push(isEth ? C('transaction', 'value', operator, value) : C('token_transfer', 'amount', operator, value));
    understood.push(amount[0].trim());
  } else if (isEth) {
    parts.push(C('transaction', 'value', 'gt', '0'));
    understood.push('ETH sent');
  }

  const source = isEth ? 'transaction' : found.length || /transfer|token/i.test(s) ? 'token_transfer' : 'transaction';
  const binance = catalog.lists.find((list) => /binance/i.test(list.name ?? ''));
  if (binance) {
    const list = { addresses: [...binance.addresses], name: binance.name };
    if (
      /\bfrom\s+(the\s+)?binance/i.test(s) ||
      /\bbinance\b.*\b(outflows?|sends?|leav)/i.test(s) ||
      /\b(outflows?|leav\w*)\b.*\bbinance/i.test(s)
    ) {
      parts.push(C(source, 'from_address', 'in', list));
      understood.push(`from ${binance.name}`);
    } else if (/\bto\s+(the\s+)?binance/i.test(s) || /\bbinance\b.*\b(inflows?|deposits?)/i.test(s)) {
      parts.push(C(source, 'to_address', 'in', list));
      understood.push(`to ${binance.name}`);
    }
  }
  const from = lower.match(/\bfrom\s+(0x[0-9a-f]{40})/);
  const to = lower.match(/\bto\s+(0x[0-9a-f]{40})/);
  if (from) {
    parts.push(C(source, 'from_address', 'eq', from[1]));
    understood.push(`from ${short(from[1])}`);
  }
  if (to) {
    parts.push(C(source, 'to_address', 'eq', to[1]));
    understood.push(`to ${short(to[1])}`);
  }

  if (!parts.length) return { condition: null, name: '', understood };
  const name = s.replace(/^(alert|tell|notify|flag|show)( me)?( when| on| if| about)?\s*/i, '').replace(/[.?!]$/, '');
  return {
    condition: normalize(G('and', ...parts)),
    name: name.charAt(0).toUpperCase() + name.slice(1),
    understood,
  };
}

export const GLYPHS: Glyph[] = [
  'triangle', 'diamond', 'target', 'square', 'star', 'bars',
  'chevron', 'bolt', 'hexagon', 'circle', 'xmark', 'ring',
];

/** The first glyph no other circuit uses, else the first. */
export function freeGlyph(used: Glyph[]): Glyph {
  return GLYPHS.find((glyph) => !used.includes(glyph)) ?? GLYPHS[0];
}

function compact(value: string): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return '';
  if (n >= 1e9) return `${+(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `${+(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${+(n / 1e3).toFixed(1)}K`;
  return String(+n.toFixed(2));
}

/**
 * A tag from what the tree tests: USDT + USDC → STABLE, a from-list gate → BNB-…-OUT,
 * the amount compacted to 2K / 1M / 1B, at most 12 characters, unique among `taken`.
 */
export function suggestTag(condition: ConditionNode | null, taken: string[], symbolOf: (address: string) => string | null): string {
  const all = condition ? leaves(condition) : [];
  const tokens = [
    ...new Set(
      all
        .filter((node) => node.field === 'token' && node.operator === 'eq')
        .map((node) => (symbolOf((node.value as { address: string }).address) ?? 'TKN').toUpperCase()),
    ),
  ];
  const parts: string[] = [];
  if (tokens.length && tokens.every((symbol) => symbol === 'USDT' || symbol === 'USDC')) parts.push('STABLE');
  else if (tokens.length === 1) parts.push(tokens[0].replace(/[^A-Z0-9]/g, '').slice(0, 5));
  else if (tokens.length > 1) parts.push(tokens.slice(0, 2).join('').replace(/[^A-Z0-9]/g, '').slice(0, 6));
  if (all.some((node) => node.field === 'token_recognised')) parts.push('UNKNOWN');
  if (all.some((node) => node.operator === 'in' && node.field === 'from_address')) {
    parts.unshift('BNB');
    parts.push('OUT');
  } else if (all.some((node) => node.operator === 'in' && node.field === 'to_address')) {
    parts.unshift('BNB');
    parts.push('IN');
  }
  const amount = all.find((node) => node.field === 'amount' && node.value !== '');
  const eth = all.find((node) => node.source === 'transaction' && node.field === 'value' && node.value !== '');
  if (eth && !tokens.length) parts.push('ETH', compact(String(eth.value)));
  else if (amount) parts.push(compact(String(amount.value)));

  const join = (ps: string[]) => ps.filter(Boolean).join('-').replace(/-+/g, '-');
  let ps = parts.filter(Boolean);
  let tag = join(ps);
  if (tag.length > 12 && (amount || eth)) {
    ps = ps.slice(0, -1);
    tag = join(ps);
  }
  if (tag.length > 12) tag = join(ps.map((part) => part.slice(0, 4)));
  tag = tag.slice(0, 12).replace(/-$/, '') || 'CIRCUIT';
  let candidate = tag;
  for (let k = 2; taken.includes(candidate); k++) candidate = `${tag.slice(0, 10)}-${k}`.slice(0, 12);
  return candidate;
}
