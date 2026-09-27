import type { Rule } from '../api/types.ts';

export const TAG_RE = /^[A-Z0-9][A-Z0-9-]{0,11}$/;
export const TAG_MAX = 12;

/** What the tag input keeps of a keystroke: uppercase, spaces become hyphens, 12 at most. */
export const normalizeTag = (input: string): string => input.toUpperCase().replace(/\s+/g, '-').slice(0, TAG_MAX);

/** The live error line under the tag input; '' when the tag is good. */
export function tagProblem(tag: string, rules: Pick<Rule, 'id' | 'tag'>[], selfId: number | null): string {
  if (!tag) return 'Give the circuit a tag, e.g. BNB-OUT.';
  if (!TAG_RE.test(tag)) return 'Tags use A–Z, 0–9 and hyphens, up to 12 characters.';
  if (rules.some((rule) => rule.id !== selfId && rule.tag === tag)) return `${tag} is already used by another circuit.`;
  return '';
}
