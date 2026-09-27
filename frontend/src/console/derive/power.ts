import type { ConditionNode, GateTrace } from '../api/types.ts';
import type { Comparison } from './describe.ts';

export type Flow = { pin: boolean; pout: boolean };

export type GatePower = {
  node: Comparison;
  /** G1, G2… in drawing order: depth first, left to right, top branch first. */
  number: number;
  held: boolean | null | undefined;
  /** Power reached the gate. */
  pin: boolean;
  /** Power reached the gate and it held, so it passed power on: the gate is live. */
  pout: boolean;
};

export type PowerPath = {
  energised: boolean;
  gates: GatePower[];
  /** Power in and out of every node, groups included, keyed by the node object. */
  flow: Map<ConditionNode, Flow>;
};

/**
 * Where power went. It enters at the left rail whenever a trace is shown; a gate is
 * live when power reaches it and it holds; AND passes power in series; OR passes it
 * if any branch is live. Every branch is walked, so dark gates are numbered too.
 */
export function powerPath(condition: ConditionNode, trace: Record<number, GateTrace> | null): PowerPath {
  const gates: GatePower[] = [];
  const flow = new Map<ConditionNode, Flow>();
  const walk = (node: ConditionNode, pin: boolean): boolean => {
    let pout: boolean;
    if (node.type === 'comparison') {
      const held = trace && node.id !== null ? trace[node.id]?.held : undefined;
      pout = pin && held === true;
      gates.push({ node, number: gates.length + 1, held, pin, pout });
    } else if (node.type === 'and') {
      pout = pin;
      for (const child of node.children) pout = walk(child, pout);
    } else {
      let any = false;
      for (const child of node.children) any = walk(child, pin) || any;
      pout = pin && any;
    }
    flow.set(node, { pin, pout });
    return pout;
  };
  const energised = walk(condition, trace !== null);
  return { energised, gates, flow };
}

/** The parent group of `node` in `root`, or null for the root itself. */
export function parentOf(root: ConditionNode, node: ConditionNode): Extract<ConditionNode, { type: 'and' | 'or' }> | null {
  if (root.type === 'comparison') return null;
  for (const child of root.children) {
    if (child === node) return root;
    const found = parentOf(child, node);
    if (found) return found;
  }
  return null;
}
