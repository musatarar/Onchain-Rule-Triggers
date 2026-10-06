import type { ConsoleApi } from './ConsoleApi.ts';
import { DemoConsoleApi } from './demo/DemoConsoleApi.ts';
import { HttpConsoleApi } from './http.ts';
import { type DemoApi, parseSources, type Sources } from './sources.ts';

export type { ConsoleApi, MatchesQuery, TokensQuery } from './ConsoleApi.ts';
export { ApiError, errorMessage } from '../../api/client.ts';

/** Every method goes to the real API, except propose and backtest set to demo in `sources`. */
export function routeApi(sources: Sources, demo: DemoApi, http: ConsoleApi): ConsoleApi {
  const pick = <K extends keyof DemoApi>(method: K): ConsoleApi[K] => {
    const target = sources[method] === 'http' ? http : demo;
    return target[method].bind(target) as ConsoleApi[K];
  };
  return {
    engineStatus: http.engineStatus.bind(http),
    vocabulary: http.vocabulary.bind(http),
    listRules: http.listRules.bind(http),
    getRule: http.getRule.bind(http),
    createRule: http.createRule.bind(http),
    updateRule: http.updateRule.bind(http),
    deleteRule: http.deleteRule.bind(http),
    matches: http.matches.bind(http),
    matchDetail: http.matchDetail.bind(http),
    tokens: http.tokens.bind(http),
    propose: pick('propose'),
    backtest: pick('backtest'),
  };
}

const sources = parseSources(import.meta.env?.VITE_CONSOLE_SOURCES);
export const api: ConsoleApi = routeApi(sources, new DemoConsoleApi(), new HttpConsoleApi());
