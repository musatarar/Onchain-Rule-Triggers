import { deleteJson, getJson, patchJson, postJson } from '../../api/client.ts';
import type { ConsoleApi, MatchesQuery, TokensQuery } from './ConsoleApi.ts';
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

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : '';
}

/** The real API, at the URLs in docs/api/frontend-contract.md. */
export class HttpConsoleApi implements ConsoleApi {
  engineStatus(): Promise<EngineStatus> {
    return getJson<EngineStatus>('/api/engine/status/');
  }

  vocabulary(): Promise<Vocabulary> {
    return getJson<Vocabulary>('/api/conditions/vocabulary/');
  }

  listRules(): Promise<Page<Rule>> {
    return getJson<Page<Rule>>('/api/rules/?page_size=100');
  }

  getRule(id: number): Promise<Rule> {
    return getJson<Rule>(`/api/rules/${id}/`);
  }

  createRule(body: RuleWrite): Promise<Rule> {
    return postJson<Rule>('/api/rules/', body);
  }

  updateRule(id: number, body: Partial<RuleWrite>): Promise<Rule> {
    return patchJson<Rule>(`/api/rules/${id}/`, body);
  }

  deleteRule(id: number): Promise<void> {
    return deleteJson(`/api/rules/${id}/`);
  }

  matches(params: MatchesQuery): Promise<JournalPage> {
    return getJson<JournalPage>(`/api/matches/${query(params)}`);
  }

  matchDetail(id: number): Promise<MatchDetail> {
    return getJson<MatchDetail>(`/api/matches/${id}/`);
  }

  tokens(params: TokensQuery): Promise<Page<TokenRef>> {
    return getJson<Page<TokenRef>>(`/api/tokens/${query(params)}`);
  }

  propose(sentence: string): Promise<Proposal> {
    return postJson<Proposal>('/api/rules/propose/', { sentence });
  }

  backtest(condition: ConditionNode): Promise<Backtest> {
    return postJson<Backtest>('/api/rules/backtest/', { condition });
  }
}
