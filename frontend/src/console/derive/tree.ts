import type { ConditionNode, Vocabulary } from '../api/types.ts';
import { type Comparison, fieldOf, type Group } from './describe.ts';

/** Composer edits. Every function returns a new tree; drafts are React state. */

export const leaves = (node: ConditionNode): Comparison[] =>
  node.type === 'comparison' ? [node] : node.children.flatMap(leaves);

export function findNode(root: ConditionNode, id: number): ConditionNode | null {
  if (root.id === id) return root;
  if (root.type === 'comparison') return null;
  for (const child of root.children) {
    const found = findNode(child, id);
    if (found) return found;
  }
  return null;
}

/** Folds a group into a same-type parent and unwraps single-child groups below the root. */
export function normalize(node: ConditionNode): ConditionNode {
  if (node.type === 'comparison') return node;
  const children = node.children
    .map(normalize)
    .flatMap((child) => (child.type === node.type ? (child as Group).children : [child]))
    .map((child) => (child.type !== 'comparison' && child.children.length === 1 ? child.children[0] : child));
  return { ...node, children };
}

/** Keeps the root a group, as the contract requires. */
const rooted = (node: ConditionNode, id: () => number): Group =>
  node.type === 'comparison' ? { id: id(), type: 'and', children: [node] } : node;

/** Wires `fresh` next to node `id`: in series ('and') after it, or on a parallel branch ('or'). */
export function addSibling(
  root: ConditionNode,
  id: number,
  kind: 'and' | 'or',
  fresh: Comparison,
  nextId: () => number,
): Group {
  const insert = (node: ConditionNode): ConditionNode => {
    if (node.id === id) return { id: nextId(), type: kind, children: [node, fresh] };
    if (node.type === 'comparison') return node;
    const index = node.children.findIndex((child) => child.id === id);
    if (index >= 0 && node.type === kind) {
      const children = [...node.children];
      children.splice(index + 1, 0, fresh);
      return { ...node, children };
    }
    return { ...node, children: node.children.map(insert) };
  };
  if (root.id === id && root.type === kind) return { ...root, children: [...(root as Group).children, fresh] };
  return rooted(normalize(insert(root)), nextId);
}

export function removeNode(root: ConditionNode, id: number, nextId: () => number): Group {
  const prune = (node: ConditionNode): ConditionNode =>
    node.type === 'comparison' ? node : { ...node, children: node.children.filter((c) => c.id !== id).map(prune) };
  return rooted(normalize(prune(root)), nextId);
}

export function replaceNode(root: ConditionNode, id: number, next: ConditionNode): ConditionNode {
  if (root.id === id) return next;
  if (root.type === 'comparison') return root;
  return { ...root, children: root.children.map((child) => replaceNode(child, id, next)) };
}

/** Gives every node without an id a temporary negative one, so a backtest's trace maps back. */
export function withDraftIds(node: ConditionNode, nextId: () => number): ConditionNode {
  const id = node.id ?? nextId();
  return node.type === 'comparison'
    ? { ...node, id }
    : { ...node, id, children: node.children.map((child) => withDraftIds(child, nextId)) };
}

/** A draft as the API takes it: temporary ids go back to null for the server to assign. */
export function forWrite(node: ConditionNode): ConditionNode {
  const id = node.id !== null && node.id > 0 ? node.id : null;
  return node.type === 'comparison' ? { ...node, id } : { ...node, id, children: node.children.map(forWrite) };
}

const ADDRESS_RE = /^0x[0-9a-f]{40}$/;
const DECIMAL_RE = /^\d+(\.\d+)?$/;

/** Every gate has a value its field can test, so the draft can be backtested and saved. */
export function isComplete(node: ConditionNode, vocabulary: Vocabulary | null): boolean {
  if (node.type !== 'comparison') return node.children.length > 0 && node.children.every((c) => isComplete(c, vocabulary));
  const value = node.value;
  switch (fieldOf(vocabulary, node).type) {
    case 'amount':
    case 'native_amount':
      return typeof value === 'string' && DECIMAL_RE.test(value);
    case 'token':
      return typeof value === 'object' && 'address' in value && ADDRESS_RE.test(value.address);
    case 'bool':
      return typeof value === 'boolean';
    case 'signature':
      return typeof value === 'string' && value.trim() !== '';
    case 'address':
      if (node.operator !== 'in') return typeof value === 'string' && ADDRESS_RE.test(value);
      return (
        typeof value === 'object' &&
        'addresses' in value &&
        value.addresses.length > 0 &&
        value.addresses.every((address) => ADDRESS_RE.test(address))
      );
  }
}
