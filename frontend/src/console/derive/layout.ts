import type { ConditionNode, GateTrace } from '../api/types.ts';
import type { Comparison, GateText, Group } from './describe.ts';
import { powerPath, type PowerPath } from './power.ts';

/** Pixels of wire power travels per second. */
export const SPEED = 820;
/** Seconds power holds on each live gate while its value flashes. */
export const HOLD = 0.6;

const WIRE_Y = 50;
const CELL_H = 86;
const GAP = 10;
const PAD = 20;
// Rough Share Tech Mono advance widths: gate subject, gate test, gate value.
const SUBJECT_W = 7.1;
const TEST_W = 7.9;
const VALUE_W = 7.0;
const LEAD = 22;
const TAIL = 16;
const MARKER = 30;
const LINE_GAP = 14;

export type GateState = 'held' | 'open' | 'unk' | 'idle';

export type SceneGate = {
  node: Comparison;
  number: number;
  x: number;
  y: number;
  w: number;
  h: number;
  cx: number;
  wy: number;
  text: GateText;
  /** What the gate read on this transaction; '' when no trace is shown. */
  value: string;
  state: GateState;
  pin: boolean;
  pout: boolean;
  /** Seconds after power leaves the left rail that it reaches this gate (live gates). */
  delay: number;
};

export type SceneWire = { x1: number; y1: number; x2: number; y2: number; lit: boolean; delay: number; duration: number };
export type SceneMarker = { x: number; wy: number; letter: string; lit: boolean; delay: number };
export type SceneCoil = { cx: number; wy: number; lit: boolean; delay: number };

export type Scene = {
  width: number;
  height: number;
  lines: number;
  traced: boolean;
  wires: SceneWire[];
  gates: SceneGate[];
  markers: SceneMarker[];
  coil: SceneCoil;
  power: PowerPath;
  /** When the last element lights, the coil's burst included (seconds). */
  duration: number;
};

export type SceneInput = {
  condition: ConditionNode;
  trace: Record<number, GateTrace> | null;
  text: (node: Comparison) => GateText;
  value: (node: Comparison) => string;
  /** The box's inner width; a wider circuit folds at its top-level AND. */
  maxWidth: number;
  /** Draws at least this wide, so the right rail sits at the box's edge. */
  minWidth: number;
  /** Phones (≤ 520px): narrower gates and coil. */
  compact: boolean;
};

type Measured =
  | { kind: 'gate'; node: Comparison; w: number; h: number; text: GateText; value: string }
  | { kind: 'and'; node: Group; w: number; h: number; kids: Measured[] }
  | { kind: 'or'; node: Group; w: number; h: number; kids: Measured[]; inner: number };

function measure(node: ConditionNode, input: SceneInput): Measured {
  if (node.type === 'comparison') {
    const text = input.text(node);
    const value = input.trace ? input.value(node) : '';
    const w = Math.max(
      input.compact ? 104 : 128,
      text.subject.length * SUBJECT_W + 26,
      text.test.length * TEST_W + 26,
      (value.length + 2) * VALUE_W + 26,
    );
    return { kind: 'gate', node, w, h: CELL_H, text, value };
  }
  const kids = node.children.map((child) => measure(child, input));
  if (node.type === 'and') {
    return { kind: 'and', node, w: kids.reduce((sum, k) => sum + k.w, 0), h: Math.max(...kids.map((k) => k.h)), kids };
  }
  const inner = Math.max(...kids.map((k) => k.w));
  return {
    kind: 'or',
    node,
    w: inner + PAD * 2,
    h: kids.reduce((sum, k) => sum + k.h, 0) + GAP * (kids.length - 1),
    kids,
    inner,
  };
}

// Before timing, every element knows its x along the unfolded wire ("gx").
type Placed<T> = T & { gx: number; len: number };

/**
 * Lays a circuit out as a ladder drawing: wires, numbered gates, OR buses, fold
 * markers and the coil, each with its lit state and, for lit elements, the moment
 * power reaches it. Power moves at SPEED and holds HOLD on every live gate, so an
 * element's delay is gx ÷ SPEED plus HOLD for each live gate before it.
 */
export function layoutCircuit(input: SceneInput): Scene {
  const { condition, trace, compact } = input;
  const power = powerPath(condition, trace);
  const root = measure(condition, input);
  const coilW = compact ? 72 : 116;
  const natural = LEAD + root.w + 40 + coilW + TAIL;

  // Wider than the box: fold at the root AND's boundaries, the way ladder drawings
  // continue on the next line under a lettered marker.
  let lines: Measured[][] = [[root]];
  if (natural > input.maxWidth && root.kind === 'and' && root.kids.length > 1) {
    const budget = input.maxWidth - LEAD - MARKER - 12 - TAIL - MARKER - 12;
    lines = [];
    let current: Measured[] = [];
    let width = 0;
    for (const kid of root.kids) {
      if (current.length && width + kid.w > budget) {
        lines.push(current);
        current = [];
        width = 0;
      }
      current.push(kid);
      width += kid.w;
    }
    lines.push(current);
    const last = lines[lines.length - 1];
    const lastW = last.reduce((sum, k) => sum + k.w, 0);
    if (lines.length > 1 && lastW + coilW + 40 > budget + MARKER && last.length > 1) lines.push([last.pop()!]);
  }
  const folded = lines.length > 1;
  const lineW = (kids: Measured[], i: number) => (i ? LEAD + MARKER + 10 : LEAD) + kids.reduce((sum, k) => sum + k.w, 0);
  const need = Math.max(
    ...lines.map((kids, i) => lineW(kids, i) + (i === lines.length - 1 ? 40 + coilW : MARKER + 20) + TAIL),
  );
  const width = Math.max(input.minWidth, folded ? Math.max(need, Math.min(input.maxWidth, need + 400)) : natural);
  const heights = lines.map((kids) => Math.max(...kids.map((k) => k.h)));
  const height = heights.reduce((sum, h) => sum + h, 0) + LINE_GAP * (lines.length - 1) + 8;

  const wires: Placed<Omit<SceneWire, 'delay' | 'duration'>>[] = [];
  const gates: Placed<Omit<SceneGate, 'delay'>>[] = [];
  const markers: Placed<Omit<SceneMarker, 'delay'>>[] = [];
  let off = 0;
  const wire = (x1: number, y1: number, x2: number, y2: number, lit: boolean) =>
    wires.push({ x1, y1, x2, y2, lit, gx: off + Math.min(x1, x2), len: Math.abs(x2 - x1) + Math.abs(y2 - y1) });
  const marker = (x: number, wy: number, letter: string, lit: boolean) =>
    markers.push({ x, wy, letter, lit, gx: off + x, len: 0 });
  const flow = (node: ConditionNode) => power.flow.get(node)!;

  const draw = (m: Measured, x: number, y: number): void => {
    if (m.kind === 'gate') {
      const { pin, pout } = flow(m.node);
      const gate = power.gates.find((g) => g.node === m.node)!;
      const cx = x + m.w / 2;
      const wy = y + WIRE_Y;
      const state: GateState = !trace ? 'idle' : gate.held === true ? 'held' : gate.held === null ? 'unk' : 'open';
      gates.push({
        node: m.node, number: gate.number, x, y, w: m.w, h: m.h, cx, wy,
        text: m.text, value: m.value, state, pin, pout, gx: off + cx, len: 0,
      });
      wire(x, wy, cx - 6, wy, pin);
      wire(cx + 6, wy, x + m.w, wy, pout);
      return;
    }
    if (m.kind === 'and') {
      let cx = x;
      for (const kid of m.kids) {
        draw(kid, cx, y);
        cx += kid.w;
      }
      return;
    }
    const { pin } = flow(m.node);
    const ys: number[] = [];
    const outs: boolean[] = [];
    let by = y;
    for (const kid of m.kids) {
      const wy = by + WIRE_Y;
      const po = flow(kid.node).pout;
      ys.push(wy);
      wire(x, wy, x + PAD, wy, pin);
      draw(kid, x + PAD, by);
      wire(x + PAD + kid.w, wy, x + PAD + m.inner, wy, po);
      wire(x + PAD + m.inner, wy, x + m.w, wy, po);
      outs.push(po);
      by += kid.h + GAP;
    }
    wire(x, ys[0], x, ys[ys.length - 1], pin);
    // Power drops back to the main line: the right bus lights down to the lowest live branch.
    wire(x + m.w, ys[0], x + m.w, ys[ys.length - 1], false);
    const lowest = outs.lastIndexOf(true);
    if (lowest > 0) wire(x + m.w, ys[0], x + m.w, ys[lowest], true);
  };

  const traced = trace !== null;
  let y = 4;
  let coil = { cx: 0, wy: 0, lit: false, gx: 0 };
  let carried = traced;
  lines.forEach((kids, i) => {
    const wy = y + WIRE_Y;
    const last = i === lines.length - 1;
    let x: number;
    if (i === 0) {
      wire(4, wy, LEAD, wy, carried);
      x = LEAD;
    } else {
      marker(LEAD - 12, wy, String.fromCharCode(64 + i), carried);
      x = LEAD + MARKER + 10;
      wire(LEAD - 12 + MARKER, wy, x, wy, carried);
    }
    for (const kid of kids) {
      draw(kid, x, y);
      carried = flow(kid.node).pout;
      x += kid.w;
    }
    if (!last) {
      const mx = width - TAIL - MARKER - 8;
      wire(x, wy, mx, wy, carried);
      marker(mx, wy, String.fromCharCode(65 + i), carried);
      off += width - LEAD - 8;
    } else {
      const cx = width - TAIL - coilW + coilW / 2;
      wire(x, wy, cx - 16, wy, carried);
      coil = { cx, wy, lit: carried, gx: off + cx - 16 };
      wire(cx + 16, wy, width - 4, wy, false);
    }
    y += heights[i] + LINE_GAP;
  });

  const pauses = gates.filter((g) => g.pout).map((g) => g.gx);
  const delayAt = (gx: number) => gx / SPEED + pauses.filter((p) => p < gx - 1).length * HOLD;
  const timedWires = wires.map(({ gx, len, ...w }) => ({
    ...w,
    delay: w.lit ? delayAt(gx) : 0,
    duration: Math.max(0.04, len / SPEED),
  }));
  const timedGates = gates.map(({ gx, len: _len, ...g }) => ({ ...g, delay: g.pout ? delayAt(gx) : 0 }));
  const timedMarkers = markers.map(({ gx, len: _len, ...m }) => ({ ...m, delay: m.lit ? delayAt(gx) : 0 }));
  const coilDelay = coil.lit ? delayAt(coil.gx) : 0;
  const duration = Math.max(
    coil.lit ? coilDelay + 0.9 : 0,
    ...timedWires.filter((w) => w.lit).map((w) => w.delay + w.duration),
    ...timedGates.filter((g) => g.pout).map((g) => g.delay + HOLD),
  );

  return {
    width,
    height,
    lines: lines.length,
    traced,
    wires: timedWires,
    gates: timedGates,
    markers: timedMarkers,
    coil: { cx: coil.cx, wy: coil.wy, lit: coil.lit, delay: coilDelay },
    power,
    duration,
  };
}
