import type { Rule } from '../api/types.ts';

type Channel = Pick<Rule, 'id' | 'tag' | 'name' | 'sentence' | 'enabled'>;

const words = (query: string): string[] =>
  query.toLowerCase().replace(/^\s*(ch|channel|tune)\s+/, '').split(/\s+/).filter(Boolean);

/** Circuits whose tag, name and sentence hold every word typed; armed first, then by id. */
export function channelMatches<T extends Channel>(rules: T[], query: string): T[] {
  const wanted = words(query);
  const hay = (rule: Channel) => `${rule.tag} ${rule.name} ${rule.sentence}`.toLowerCase();
  return rules
    .filter((rule) => wanted.every((word) => hay(rule).includes(word)))
    .sort((a, b) => Number(b.enabled) - Number(a.enabled) || a.id - b.id);
}

/** ALL stays listed until the query rules it out ("a", "al", "all" keep it). */
export const showsAll = (query: string): boolean => !query.trim() || 'all'.startsWith(query.trim().toLowerCase());

/** What Enter tunes to: ALL, the top circuit, or nothing when nothing matches. */
export function tuneTarget(rules: Channel[], query: string): 'all' | number | null {
  if (showsAll(query)) return 'all';
  return channelMatches(rules, query)[0]?.id ?? null;
}
