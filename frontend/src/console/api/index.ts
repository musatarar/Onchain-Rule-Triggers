import type { ConsoleApi } from './ConsoleApi.ts';
import { DemoConsoleApi } from './demo/DemoConsoleApi.ts';
import { HttpConsoleApi } from './http.ts';
import { parseSources, SOURCE_OF, type Sources } from './sources.ts';

export type { ConsoleApi, MatchesQuery, TokensQuery } from './ConsoleApi.ts';
export { ApiError, errorMessage } from '../../api/client.ts';

/** Each method goes to the demo or the real API, per its key in `sources`. */
export function routeApi(sources: Sources, demo: ConsoleApi, http: ConsoleApi): ConsoleApi {
  const pick = <K extends keyof ConsoleApi>(method: K): ConsoleApi[K] => {
    const target = sources[SOURCE_OF[method]] === 'http' ? http : demo;
    return target[method].bind(target) as ConsoleApi[K];
  };
  return {
    engineStatus: pick('engineStatus'),
    vocabulary: pick('vocabulary'),
    listRules: pick('listRules'),
    getRule: pick('getRule'),
    createRule: pick('createRule'),
    updateRule: pick('updateRule'),
    deleteRule: pick('deleteRule'),
    matches: pick('matches'),
    matchDetail: pick('matchDetail'),
    tokens: pick('tokens'),
    propose: pick('propose'),
    backtest: pick('backtest'),
  };
}

const sources = parseSources(import.meta.env?.VITE_CONSOLE_SOURCES);
export const api: ConsoleApi = routeApi(sources, new DemoConsoleApi(), new HttpConsoleApi());
