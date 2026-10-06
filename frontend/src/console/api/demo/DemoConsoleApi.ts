import { ApiError } from '../../../api/client.ts';
import type { DemoApi } from '../sources.ts';
import type {
  Backtest,
  BacktestRow,
  ConditionNode,
  GateTrace,
  Glyph,
  JournalRow,
  Proposal,
  Rule,
  TokenRef,
  Vocabulary,
} from '../types.ts';
import { DECIMAL_RE, scaled } from './decimal.ts';
import { evaluate, type FixtureTransaction, type Lookup, madeTransfer, type TransferTransaction } from './evaluate.ts';
import { freeGlyph, leaves, parseSentence, suggestTag, type ParserCatalog } from './parse.ts';
import labelsFixture from './fixtures/address-labels.json' with { type: 'json' };
import blocksFixture from './fixtures/blocks.json' with { type: 'json' };
import circuitsFixture from './fixtures/circuits.json' with { type: 'json' };
import tokensFixture from './fixtures/tokens.json' with { type: 'json' };
import transactionsFixture from './fixtures/transactions.json' with { type: 'json' };
import vocabularyFixture from './fixtures/vocabulary.json' with { type: 'json' };

const TRANSACTIONS = transactionsFixture as unknown as FixtureTransaction[];
const TOKENS = tokensFixture as TokenRef[];
const VOCABULARY = vocabularyFixture as Vocabulary;
const CIRCUITS = circuitsFixture as Omit<Rule, 'stats'>[];
const BLOCKS = blocksFixture;
const PAGE_SIZE = 50;

const clone = <T>(value: T): T => structuredClone(value);

/** Throws what `client.ts` would have thrown for the same DRF response body. */
function fail(status: number, body: Record<string, unknown>): never {
  const code = typeof body.code === 'string' ? body.code : '';
  if (typeof body.detail === 'string') throw new ApiError(body.detail, status, code);
  const [field, value] = Object.entries(body)[0];
  throw new ApiError(`${field}: ${Array.isArray(value) ? String(value[0]) : String(value)}`, status, code);
}

const ADDRESS_RE = /^0x[0-9a-f]{40}$/;

/** The server's `validate_conditions`: the first problem, as a path and a sentence. */
function conditionProblem(node: ConditionNode, path = 'condition', root = true): string | null {
  if (node.type !== 'comparison') {
    if (!node.children.length) return `${path}: a group needs at least one condition.`;
    for (const [i, child] of node.children.entries()) {
      const problem = conditionProblem(child, `${path}.children[${i}]`, false);
      if (problem) return problem;
    }
    return null;
  }
  if (root) return `${path}: the root must be an "and" or "or" group.`;
  const source = VOCABULARY.sources.find((s) => s.key === node.source);
  if (!source) return `${path}.source: unknown source '${node.source}'.`;
  const field = source.fields.find((f) => f.key === node.field);
  if (!field) return `${path}.field: '${node.source}' has no field '${node.field}'.`;
  if (!field.operators.includes(node.operator)) return `${path}.operator: '${node.field}' does not take '${node.operator}'.`;
  const value = node.value;
  const bad = (what: string) => `${path}.value: ${what}.`;
  switch (field.type) {
    case 'amount':
      return typeof value === 'string' && DECIMAL_RE.test(value) ? null : bad('enter an amount such as 250 or 0.5');
    case 'token':
      return typeof value === 'object' && value !== null && 'address' in value && ADDRESS_RE.test(value.address)
        ? null
        : bad('pick a token');
    case 'bool':
      return typeof value === 'boolean' ? null : bad('must be true or false');
    case 'address':
      if (node.operator === 'in') {
        const list = value as { addresses?: unknown };
        return typeof value === 'object' && value !== null && Array.isArray(list.addresses) && list.addresses.length &&
          list.addresses.every((a) => typeof a === 'string' && ADDRESS_RE.test(a))
          ? null
          : bad('list at least one 0x address');
      }
      return typeof value === 'string' && ADDRESS_RE.test(value) ? null : bad('enter a lowercase 0x address');
  }
  return null;
}

const delay = (min: number, max: number) =>
  new Promise<void>((resolve) => setTimeout(resolve, min + Math.random() * (max - min)));

/**
 * Propose and backtest, in memory, over the demo fixtures, for as long as the
 * server has no endpoint for them: the same shapes and errors the contract
 * promises, with a little latency so loading states are real. Proposals read the
 * demo circuits' tags, glyphs and address lists, and backtests scan the demo's
 * five blocks.
 */
export class DemoConsoleApi implements DemoApi {
  private readonly latency: [number, number];
  private readonly tokenIndex = new Map(TOKENS.map((token) => [`${token.chain}:${token.address}`, token]));
  private readonly labels = new Map(labelsFixture.map((entry) => [entry.address, entry.label]));
  private readonly lookup: Lookup = {
    token: (chain, address) =>
      this.tokenIndex.get(`${chain}:${address}`) ?? { chain, address, symbol: null, name: null, decimals: null },
    label: (address) => this.labels.get(address) ?? null,
  };
  private readonly catalog: ParserCatalog;

  constructor(options: { latency?: [number, number] } = {}) {
    this.latency = options.latency ?? [120, 300];
    const lists = CIRCUITS.flatMap((rule) => leaves(rule.condition))
      .filter((node) => node.operator === 'in')
      .map((node) => node.value as { addresses: string[]; name?: string });
    this.catalog = { chain: 1, tokens: TOKENS, lists };
  }

  private rowBase(tx: TransferTransaction): Omit<JournalRow, 'id' | 'rule' | 'rule_revision'> {
    const transfer = tx.transfer;
    const token = this.lookup.token(tx.chain, transfer.token_address);
    const from = transfer.from_address;
    const to = transfer.to_address;
    const raw = transfer.raw_value;
    const decimals = token.decimals;
    return {
      matched_at: tx.block_timestamp,
      transaction: {
        chain: tx.chain,
        hash: tx.hash,
        block_number: tx.block_number,
        transaction_index: tx.transaction_index,
        block_timestamp: tx.block_timestamp,
      },
      headline: {
        kind: 'token_transfer',
        from_address: from,
        to_address: to,
        from_label: this.lookup.label(from),
        to_label: this.lookup.label(to),
        amount: { raw, decimals, value: decimals === null ? null : scaled(raw, decimals) },
        token,
      },
      flags: {
        token_unrecognised: token.symbol === null,
        decimals_unknown: token.decimals === null,
        verified: transfer.verified,
      },
    };
  }

  private async wait(): Promise<void> {
    const [min, max] = this.latency;
    if (max > 0) await delay(min, max);
  }

  async propose(sentence: string): Promise<Proposal> {
    await this.wait();
    const parsed = parseSentence(sentence, this.catalog);
    if (!parsed.condition) fail(422, { detail: "Couldn't turn that into conditions.", code: 'not_understood' });
    const symbolOf = (address: string) => TOKENS.find((token) => token.address === address)?.symbol ?? null;
    return {
      condition: parsed.condition,
      name: parsed.name,
      tag: suggestTag(parsed.condition, CIRCUITS.map((rule) => rule.tag), symbolOf),
      glyph: freeGlyph(CIRCUITS.map((rule) => rule.glyph)) as Glyph,
      understood: parsed.understood,
    };
  }

  async backtest(condition: ConditionNode): Promise<Backtest> {
    await this.wait();
    const problem = conditionProblem(condition);
    if (problem) fail(400, { condition: [problem] });
    let matchCount = 0;
    let unevaluable = 0;
    const matches: BacktestRow[] = [];
    for (const tx of TRANSACTIONS) {
      if (!madeTransfer(tx)) continue;
      const trace: Record<number, GateTrace> = {};
      const held = evaluate(condition, tx, this.lookup, trace);
      if (held === null) unevaluable++;
      if (held !== true) continue;
      matchCount++;
      if (matches.length < PAGE_SIZE) matches.push({ rule_revision: 0, ...this.rowBase(tx), trace });
    }
    return clone({
      window: { chain: BLOCKS[0].chain, first_block: BLOCKS[0].number, last_block: BLOCKS[BLOCKS.length - 1].number },
      transactions_scanned: TRANSACTIONS.length,
      match_count: matchCount,
      unevaluable_count: unevaluable,
      matches,
    });
  }
}
