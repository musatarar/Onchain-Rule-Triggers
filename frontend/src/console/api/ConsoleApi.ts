import type {
  Backtest,
  ConditionNode,
  EngineStatus,
  JournalPage,
  MatchDetail,
  Page,
  Proposal,
  Rule,
  RuleWrite,
  TokenRef,
  Vocabulary,
} from './types.ts';

export type MatchesQuery = {
  rule?: number;
  cursor?: string;
  /** Only rows newer than this cursor (a page's `head`), for polling. */
  after?: string;
  page_size?: number;
};

export type TokensQuery = { q: string; chain?: number };

/** One method per endpoint in docs/api/frontend-contract.md. */
export interface ConsoleApi {
  engineStatus(): Promise<EngineStatus>;
  vocabulary(): Promise<Vocabulary>;
  listRules(): Promise<Page<Rule>>;
  getRule(id: number): Promise<Rule>;
  createRule(body: RuleWrite): Promise<Rule>;
  updateRule(id: number, body: Partial<RuleWrite>): Promise<Rule>;
  deleteRule(id: number): Promise<void>;
  matches(query: MatchesQuery): Promise<JournalPage>;
  matchDetail(id: number): Promise<MatchDetail>;
  tokens(query: TokensQuery): Promise<Page<TokenRef>>;
  propose(sentence: string): Promise<Proposal>;
  backtest(condition: ConditionNode): Promise<Backtest>;
}
