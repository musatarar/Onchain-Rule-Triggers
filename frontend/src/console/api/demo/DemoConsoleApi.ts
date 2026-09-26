import { ApiError } from '../../../api/client.ts';
import type { ConsoleApi, MatchesQuery, TokensQuery } from '../ConsoleApi.ts';
import type {
  Backtest,
  BacktestRow,
  ConditionNode,
  EngineStatus,
  Glyph,
  GateTrace,
  JournalPage,
  JournalRow,
  MatchDetail,
  Page,
  Proposal,
  Rule,
  RuleRef,
  RuleWrite,
  TokenRef,
  Vocabulary,
} from '../types.ts';
import { DECIMAL_RE, scaled } from './decimal.ts';
import { evaluate, type FixtureTransaction, type Lookup } from './evaluate.ts';
import { freeGlyph, GLYPHS, leaves, parseSentence, suggestTag, type ParserCatalog } from './parse.ts';
import labelsFixture from './fixtures/address-labels.json' with { type: 'json' };
import blocksFixture from './fixtures/blocks.json' with { type: 'json' };
import circuitsFixture from './fixtures/circuits.json' with { type: 'json' };
import tokensFixture from './fixtures/tokens.json' with { type: 'json' };
import transactionsFixture from './fixtures/transactions.json' with { type: 'json' };
import vocabularyFixture from './fixtures/vocabulary.json' with { type: 'json' };

type StoredRule = Omit<Rule, 'stats'>;
type Hit = { id: number; rule: StoredRule; ordinal: number; trace: Record<number, GateTrace>; snapshot: ConditionNode };

const TRANSACTIONS = transactionsFixture as unknown as FixtureTransaction[];
const TOKENS = tokensFixture as TokenRef[];
const VOCABULARY = vocabularyFixture as Vocabulary;
const BLOCKS = blocksFixture;
const CHAIN_NAMES: Record<number, string> = { 1: 'Ethereum' };
const TAG_RE = /^[A-Z0-9][A-Z0-9-]{0,11}$/;
const PAGE_SIZE = 50;
// Match ids are deterministic from (circuit id, tx hash): the tx's place in the fixture.
const ID_STRIDE = 10_000;

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
    case 'native_amount':
      return typeof value === 'string' && DECIMAL_RE.test(value) ? null : bad('enter an amount such as 250 or 0.5');
    case 'token':
      return typeof value === 'object' && value !== null && 'address' in value && ADDRESS_RE.test(value.address)
        ? null
        : bad('pick a token');
    case 'bool':
      return typeof value === 'boolean' ? null : bad('must be true or false');
    case 'signature':
      return typeof value === 'string' && value.trim() ? null : bad('enter a method name or selector');
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

const stripIds = (node: ConditionNode): unknown =>
  node.type === 'comparison' ? { ...node, id: null } : { ...node, id: null, children: node.children.map(stripIds) };

const delay = (min: number, max: number) =>
  new Promise<void>((resolve) => setTimeout(resolve, min + Math.random() * (max - min)));

const refOf = (rule: StoredRule): RuleRef => ({ id: rule.id, name: rule.name, tag: rule.tag, glyph: rule.glyph });

// Journal order: block desc, then tx index desc, then circuit id.
function journalOrder(a: Hit, b: Hit): number {
  const x = TRANSACTIONS[a.ordinal];
  const y = TRANSACTIONS[b.ordinal];
  return y.block_number - x.block_number || y.transaction_index - x.transaction_index || a.rule.id - b.rule.id;
}

const cursorOf = (hit: Hit) => {
  const tx = TRANSACTIONS[hit.ordinal];
  return `${tx.block_number}.${tx.transaction_index}.${hit.rule.id}`;
};

/** Where a hit sits against a cursor in journal order: negative when it is newer. */
function againstCursor(hit: Hit, cursor: string): number {
  const [block, index, rule] = cursor.split('.').map(Number);
  const tx = TRANSACTIONS[hit.ordinal];
  return block - tx.block_number || index - tx.transaction_index || hit.rule.id - rule;
}

/**
 * The console's backend, in memory, over the demo fixtures: the same shapes, ids,
 * ordering and errors the contract promises, with a little latency so loading
 * states are real.
 */
export class DemoConsoleApi implements ConsoleApi {
  private rules: StoredRule[];
  private hits: Hit[] = [];
  private byId = new Map<number, Hit>();
  private stats = new Map<number, Rule['stats']>();
  private nextRuleId: number;
  private nextNodeId: number;
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
    this.rules = clone(circuitsFixture as StoredRule[]);
    this.nextRuleId = Math.max(...this.rules.map((rule) => rule.id)) + 1;
    this.nextNodeId = Math.max(...this.rules.flatMap((rule) => this.nodeIds(rule.condition))) + 1;
    const lists = this.rules
      .flatMap((rule) => leaves(rule.condition))
      .filter((node) => node.operator === 'in')
      .map((node) => node.value as { addresses: string[]; name?: string });
    this.catalog = { chain: 1, tokens: TOKENS, lists };
    this.recompute();
  }

  // ----- the engine ------------------------------------------------------

  private nodeIds(node: ConditionNode): number[] {
    const own = node.id === null ? [] : [node.id];
    return node.type === 'comparison' ? own : [...own, ...node.children.flatMap((child) => this.nodeIds(child))];
  }

  /** Gives every new node (null or a client's temporary negative id) a server id. */
  private assignIds(node: ConditionNode): ConditionNode {
    const id = node.id !== null && node.id > 0 ? node.id : this.nextNodeId++;
    return node.type === 'comparison'
      ? { ...node, id }
      : { ...node, id, children: node.children.map((child) => this.assignIds(child)) };
  }

  private recompute(): void {
    this.hits = [];
    this.stats.clear();
    for (const rule of this.rules) {
      let unevaluable = 0;
      let last: string | null = null;
      let count = 0;
      if (rule.enabled) {
        for (const [ordinal, tx] of TRANSACTIONS.entries()) {
          const trace: Record<number, GateTrace> = {};
          const held = evaluate(rule.condition, tx, this.lookup, trace);
          if (held === null) unevaluable++;
          if (held !== true) continue;
          this.hits.push({ id: rule.id * ID_STRIDE + ordinal, rule, ordinal, trace, snapshot: rule.condition });
          count++;
          if (!last || tx.block_timestamp > last) last = tx.block_timestamp;
        }
      }
      this.stats.set(rule.id, { match_count: count, unevaluable_count: unevaluable, last_match_at: last });
    }
    this.hits.sort(journalOrder);
    this.byId = new Map(this.hits.map((hit) => [hit.id, hit]));
  }

  private withStats(rule: StoredRule): Rule {
    return clone({ ...rule, stats: this.stats.get(rule.id)! });
  }

  private rowBase(tx: FixtureTransaction): Omit<JournalRow, 'id' | 'rule' | 'rule_revision'> {
    const transfer = tx.transfer;
    const token = transfer ? this.lookup.token(tx.chain, transfer.token_address) : null;
    const from = transfer ? transfer.from_address : tx.from_address;
    const to = transfer ? transfer.to_address : tx.to_address;
    const raw = transfer ? transfer.raw_value : tx.value;
    const decimals = token ? token.decimals : 18;
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
        kind: transfer ? 'token_transfer' : 'native',
        from_address: from,
        to_address: to,
        from_label: this.lookup.label(from),
        to_label: to ? this.lookup.label(to) : null,
        amount: { raw, decimals, value: decimals === null ? null : scaled(raw, decimals) },
        token,
      },
      flags: {
        token_unrecognised: token !== null && token.symbol === null,
        decimals_unknown: token !== null && token.decimals === null,
        verified: transfer ? transfer.verified : false,
      },
    };
  }

  private journalRow(hit: Hit): JournalRow {
    return {
      id: hit.id,
      rule: refOf(hit.rule),
      rule_revision: hit.rule.revision,
      ...this.rowBase(TRANSACTIONS[hit.ordinal]),
    };
  }

  private async wait(): Promise<void> {
    const [min, max] = this.latency;
    if (max > 0) await delay(min, max);
  }

  private checkWrite(body: Partial<RuleWrite>, selfId: number | null): void {
    if (body.name !== undefined && !body.name.trim()) fail(400, { name: ['This field may not be blank.'] });
    if (body.tag !== undefined) {
      if (!TAG_RE.test(body.tag)) fail(400, { tag: ['Tags use A–Z, 0–9 and hyphens, up to 12 characters.'] });
      if (this.rules.some((rule) => rule.id !== selfId && rule.tag === body.tag)) {
        fail(400, { tag: [`${body.tag} is already used by another circuit.`] });
      }
    }
    if (body.glyph !== undefined && !GLYPHS.includes(body.glyph)) fail(400, { glyph: [`"${body.glyph}" is not a valid choice.`] });
    if (body.condition !== undefined) {
      const problem = conditionProblem(body.condition);
      if (problem) fail(400, { condition: [problem] });
    }
  }

  // ----- the contract ----------------------------------------------------

  async engineStatus(): Promise<EngineStatus> {
    await this.wait();
    const first = BLOCKS[0];
    const last = BLOCKS[BLOCKS.length - 1];
    return {
      chains: [
        {
          chain: first.chain,
          name: CHAIN_NAMES[first.chain] ?? `Chain ${first.chain}`,
          first_block: first.number,
          last_block: last.number,
          last_block_at: last.timestamp,
        },
      ],
      rules: { total: this.rules.length, enabled: this.rules.filter((rule) => rule.enabled).length },
      match_count: this.hits.length,
    };
  }

  async vocabulary(): Promise<Vocabulary> {
    await this.wait();
    return clone(VOCABULARY);
  }

  async listRules(): Promise<Page<Rule>> {
    await this.wait();
    const results = this.rules.map((rule) => this.withStats(rule));
    return { count: results.length, next: null, previous: null, results };
  }

  async getRule(id: number): Promise<Rule> {
    await this.wait();
    const rule = this.rules.find((candidate) => candidate.id === id);
    if (!rule) fail(404, { detail: 'Not found.', code: 'not_found' });
    return this.withStats(rule);
  }

  async createRule(body: RuleWrite): Promise<Rule> {
    await this.wait();
    this.checkWrite(body, null);
    const now = new Date().toISOString();
    const rule: StoredRule = {
      id: this.nextRuleId++,
      name: body.name.trim(),
      tag: body.tag,
      glyph: body.glyph,
      sentence: body.sentence,
      enabled: body.enabled,
      revision: 1,
      condition: this.assignIds(clone(body.condition)),
      created_at: now,
      updated_at: now,
    };
    this.rules.push(rule);
    this.recompute();
    return this.withStats(rule);
  }

  async updateRule(id: number, body: Partial<RuleWrite>): Promise<Rule> {
    await this.wait();
    const index = this.rules.findIndex((candidate) => candidate.id === id);
    if (index < 0) fail(404, { detail: 'Not found.', code: 'not_found' });
    this.checkWrite(body, id);
    const current = this.rules[index];
    const next: StoredRule = { ...current, ...clone(body), updated_at: new Date().toISOString() };
    if (body.name !== undefined) next.name = body.name.trim();
    if (body.condition !== undefined) {
      next.condition = this.assignIds(clone(body.condition));
      const changed = JSON.stringify(stripIds(next.condition)) !== JSON.stringify(stripIds(current.condition));
      if (changed) next.revision = current.revision + 1;
    }
    this.rules[index] = next;
    this.recompute();
    return this.withStats(next);
  }

  async deleteRule(id: number): Promise<void> {
    await this.wait();
    if (!this.rules.some((rule) => rule.id === id)) fail(404, { detail: 'Not found.', code: 'not_found' });
    this.rules = this.rules.filter((rule) => rule.id !== id);
    this.recompute();
  }

  async matches(query: MatchesQuery): Promise<JournalPage> {
    await this.wait();
    const size = query.page_size ?? PAGE_SIZE;
    if (!Number.isInteger(size) || size < 1 || size > 100) fail(400, { page_size: ['Use a page size from 1 to 100.'] });
    let rows = query.rule === undefined ? this.hits : this.hits.filter((hit) => hit.rule.id === query.rule);
    const head = rows.length ? cursorOf(rows[0]) : '';
    if (query.after) rows = rows.filter((hit) => againstCursor(hit, query.after!) < 0);
    else if (query.cursor) rows = rows.filter((hit) => againstCursor(hit, query.cursor!) > 0);
    const page = rows.slice(0, size);
    return {
      results: page.map((hit) => this.journalRow(hit)),
      next: rows.length > size ? cursorOf(page[page.length - 1]) : null,
      head,
    };
  }

  async matchDetail(id: number): Promise<MatchDetail> {
    await this.wait();
    const hit = this.byId.get(id);
    if (!hit) fail(404, { detail: 'Not found.', code: 'not_found' });
    const tx = TRANSACTIONS[hit.ordinal];
    const row = this.journalRow(hit);
    const transfer = tx.transfer;
    return clone({
      ...row,
      condition: hit.snapshot,
      trace: hit.trace,
      transaction: {
        ...row.transaction,
        from_address: tx.from_address,
        to_address: tx.to_address,
        value: tx.value,
        input_selector: tx.input_selector,
        method: tx.method,
        decode_status: tx.decode_status,
      },
      transfer: transfer
        ? {
            token: this.lookup.token(tx.chain, transfer.token_address),
            from_address: transfer.from_address,
            to_address: transfer.to_address,
            raw_value: transfer.raw_value,
            log_index: transfer.log_index,
            source: transfer.source,
            verified: transfer.verified,
          }
        : null,
      also_matched: this.hits
        .filter((other) => other.ordinal === hit.ordinal && other.id !== hit.id)
        .sort((a, b) => a.rule.id - b.rule.id)
        .map((other) => ({ match_id: other.id, rule: refOf(other.rule) })),
    });
  }

  async tokens(query: TokensQuery): Promise<Page<TokenRef>> {
    await this.wait();
    const q = query.q.trim().toLowerCase();
    const rank = (token: TokenRef) =>
      !q ? 0 : token.symbol?.toLowerCase() === q ? 0 : token.symbol?.toLowerCase().startsWith(q) ? 1 : 2;
    const results = TOKENS.filter(
      (token) =>
        (query.chain === undefined || token.chain === query.chain) &&
        (!q ||
          token.symbol?.toLowerCase().includes(q) ||
          token.name?.toLowerCase().includes(q) ||
          token.address.startsWith(q)),
    ).sort((a, b) => rank(a) - rank(b) || (a.symbol ?? '~').localeCompare(b.symbol ?? '~'));
    const page = results.slice(0, PAGE_SIZE);
    return { count: results.length, next: results.length > PAGE_SIZE ? '/api/tokens/?page=2' : null, previous: null, results: clone(page) };
  }

  async propose(sentence: string): Promise<Proposal> {
    await this.wait();
    const parsed = parseSentence(sentence, this.catalog);
    if (!parsed.condition) fail(422, { detail: "Couldn't turn that into conditions.", code: 'not_understood' });
    const symbolOf = (address: string) => TOKENS.find((token) => token.address === address)?.symbol ?? null;
    return {
      condition: parsed.condition,
      name: parsed.name,
      tag: suggestTag(parsed.condition, this.rules.map((rule) => rule.tag), symbolOf),
      glyph: freeGlyph(this.rules.map((rule) => rule.glyph)) as Glyph,
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
