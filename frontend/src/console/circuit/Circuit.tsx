import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import type { ConditionNode, GateTrace, Glyph } from '../api/types.ts';
import { type Comparison, describeGate, type Describer, gateSentence } from '../derive/describe.ts';
import { observedText } from '../derive/explain.ts';
import { layoutCircuit, type Scene, type SceneGate } from '../derive/layout.ts';
import { glyphPaths } from './Glyph.tsx';

const VALUE_W = 7.0;
const MARKER = 30;

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const list = window.matchMedia(query);
    const update = () => setMatches(list.matches);
    update();
    list.addEventListener('change', update);
    return () => list.removeEventListener('change', update);
  }, [query]);
  return matches;
}

/** The content width of an element, kept current as it resizes. */
export function useBoxWidth<T extends HTMLElement>(): [(element: T | null) => void, number] {
  const [width, setWidth] = useState(0);
  const observer = useRef<ResizeObserver | null>(null);
  const ref = useCallback((element: T | null) => {
    observer.current?.disconnect();
    if (!element) return;
    observer.current = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.current.observe(element);
  }, []);
  useEffect(() => () => observer.current?.disconnect(), []);
  return [ref, width];
}

/** Lays the circuit out for the box it sits in; null until the box has a width. */
export function useScene(
  condition: ConditionNode | null,
  trace: Record<number, GateTrace> | null,
  width: number,
  describer: Describer,
): Scene | null {
  const compact = useMediaQuery('(max-width: 520px)');
  return useMemo(() => {
    if (!condition || width < 120 || condition.type === 'comparison') return null;
    return layoutCircuit({
      condition,
      trace,
      text: (node: Comparison) => describeGate(node, describer),
      value: (node: Comparison) => observedText(node.id === null ? undefined : trace?.[node.id]),
      maxWidth: width,
      minWidth: width,
      compact,
    });
  }, [condition, trace, width, describer, compact]);
}

/**
 * Whether the power-on animation is running. A new `playKey` starts it; it stops by
 * itself once the coil has burst, and at once if the box is resized.
 */
export function usePlayback(playKey: string | null, scene: Scene | null, width: number): { playing: boolean; key: string } {
  const reduced = useMediaQuery('(prefers-reduced-motion: reduce)');
  const [run, setRun] = useState<{ key: string; width: number; done: boolean }>({ key: 'static', width: 0, done: true });
  const duration = scene?.duration ?? 0;
  useEffect(() => {
    if (!playKey || !scene || reduced) return;
    setRun({ key: playKey, width, done: false });
    const timer = window.setTimeout(() => setRun((current) => ({ ...current, done: true })), (duration + 0.4) * 1000);
    return () => window.clearTimeout(timer);
    // Only a new key replays (once its scene exists); later renders never do.
  }, [playKey, scene !== null]);
  return { playing: !run.done && run.key === playKey && run.width === width, key: run.key };
}

type Pick = number | 'coil';

type SvgProps = {
  scene: Scene;
  tag: string;
  glyph: Glyph | null;
  /** The trace view: bigger text, heavier lit wires, taller contact bars. */
  big?: boolean;
  playing?: boolean;
  /** Gates (and in inspect mode the coil) become buttons. */
  mode?: 'inspect' | 'edit' | null;
  selected?: Pick | null;
  onPick?: (pick: Pick) => void;
  label: string;
  boxWidth: number;
};

/** The moment power reaches an element (`--d`) and how long its wire takes to draw (`--t`). */
function timing(delay: number, duration?: number): CSSProperties {
  const style: Record<string, string> = { '--d': `${delay.toFixed(3)}s` };
  if (duration !== undefined) style['--t'] = `${duration.toFixed(3)}s`;
  return style as CSSProperties;
}

function activateOnKey(event: KeyboardEvent, action: () => void) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    action();
  }
}

function GateView({
  gate,
  scene,
  big,
  mode,
  selected,
  onPick,
}: {
  gate: SceneGate;
  scene: Scene;
  big: boolean;
  mode: SvgProps['mode'];
  selected: boolean;
  onPick?: (pick: Pick) => void;
}) {
  const { x, y, w, h, cx, wy, text, value, state, pout } = gate;
  const id = gate.node.id;
  const bar = big ? 16 : 11;
  const interactive = mode && id !== null;
  const verb = mode === 'edit' ? 'Edit' : 'Inspect';
  const props = interactive
    ? {
        role: 'button',
        tabIndex: 0,
        'aria-pressed': selected,
        'aria-label': `${verb} gate G${gate.number}: ${gateSentence(text)}`,
        onClick: () => onPick?.(id),
        onKeyDown: (event: KeyboardEvent) => activateOnKey(event, () => onPick?.(id)),
      }
    : {};
  return (
    <g
      className={`ctc-g ${state}${pout ? ' pout' : ''}${interactive ? ' ctc' : ''}`}
      style={pout ? timing(gate.delay) : undefined}
      {...props}
    >
      {interactive && <rect className="hit" x={x + 3} y={y + 2} width={w - 6} height={h - 4} rx={2} />}
      <text className="gi" x={x + 4} y={y + 45}>
        G{gate.number}
      </text>
      {selected && <rect className="selbox" x={x + 3} y={y + 2} width={w - 6} height={h - 4} rx={2} />}
      <text className="t1" x={cx} y={y + 17} textAnchor="middle">
        {text.subject}
      </text>
      <text className="t2" x={cx} y={y + 33} textAnchor="middle">
        {text.test}
      </text>
      {state === 'held' && <rect className="fillc" x={cx - 5} y={wy - 1.75} width={10} height={3.5} />}
      <path className="bar" d={`M${cx - 6} ${wy - bar}V${wy + bar}M${cx + 6} ${wy - bar}V${wy + bar}`} />
      {state === 'unk' && (
        <text className="q" x={cx} y={wy + 4} textAnchor="middle">
          ?
        </text>
      )}
      {scene.traced && pout && <rect className="pulse" x={x + 3} y={y + 2} width={w - 6} height={h - 4} />}
      {scene.traced && pout && (
        <rect
          className="hl"
          x={cx - (value.length * VALUE_W) / 2 - 5}
          y={y + 66}
          width={value.length * VALUE_W + 10}
          height={17}
        />
      )}
      {scene.traced && (
        <text className={`act${pout ? ' boxed' : ''}`} x={cx} y={y + 78} textAnchor="middle">
          {value}
        </text>
      )}
    </g>
  );
}

/** The ladder drawing of one circuit, as laid out by `layoutCircuit`. */
export function CircuitSvg({
  scene,
  tag,
  glyph,
  big = false,
  playing = false,
  mode = null,
  selected = null,
  onPick,
  label,
  boxWidth,
}: SvgProps) {
  const { width, height, coil } = scene;
  // Up to 30% too wide draws scaled to fit; wider than that, the box scrolls.
  const scale = width > boxWidth && boxWidth / width >= 0.7 ? boxWidth / width : 1;
  const c = coil.cx;
  const wy = coil.wy;
  const coilPick = mode === 'inspect';
  const lit = coil.lit;
  return (
    <svg
      className={`rg${playing ? ' anim' : ''}${big ? ' trace' : ''}`}
      width={Math.floor(width * scale)}
      height={Math.round(height * scale)}
      viewBox={`0 0 ${width} ${height}`}
      role={mode ? 'group' : 'img'}
      aria-label={label}
    >
      <line className={`rail${scene.traced ? ' on' : ''}`} x1={4} y1={2} x2={4} y2={height - 2} />
      {scene.wires.map((w, i) => {
        const d = `M${w.x1} ${w.y1}L${w.x2} ${w.y2}`;
        if (w.lit && playing) {
          return (
            <g key={i}>
              <path className="w base" d={d} />
              <path className="w on" d={d} pathLength={1} style={timing(w.delay, w.duration)} />
            </g>
          );
        }
        return <path key={i} className={`w${w.lit ? ' on' : ''}`} d={d} />;
      })}
      {scene.markers.map((m) => (
        <g key={`${m.letter}${m.x}`} className={`mk${m.lit ? ' on' : ''}`} style={m.lit ? timing(m.delay) : undefined}>
          <path d={`M${m.x} ${m.wy - 9}h${MARKER - 9}l9 9l-9 9h-${MARKER - 9}z`} />
          <text x={m.x + (MARKER - 9) / 2 + 1} y={m.wy + 4} textAnchor="middle">
            {m.letter}
          </text>
        </g>
      ))}
      {scene.gates.map((gate) => (
        <GateView
          key={gate.number}
          gate={gate}
          scene={scene}
          big={big}
          mode={mode}
          selected={gate.node.id !== null && selected === gate.node.id}
          onPick={onPick}
        />
      ))}
      <g
        className={`coil-g${lit ? ' on' : ''}${coilPick ? ' ctc' : ''}`}
        style={lit ? timing(coil.delay) : undefined}
        {...(coilPick
          ? {
              role: 'button',
              tabIndex: 0,
              'aria-pressed': selected === 'coil',
              'aria-label': `Inspect the coil: what energised ${tag}`,
              onClick: () => onPick?.('coil'),
              onKeyDown: (event: KeyboardEvent) => activateOnKey(event, () => onPick?.('coil')),
            }
          : {})}
      >
        {coilPick && <rect className="hit" x={c - 52} y={wy - 40} width={104} height={82} rx={2} />}
        {selected === 'coil' && <rect className="selbox" x={c - 52} y={wy - 40} width={104} height={82} rx={2} />}
        <circle className={`coilfill${lit ? ' on' : ''}`} cx={c} cy={wy} r={14} />
        <path
          className={`coil${lit ? ' on' : ''}`}
          d={`M${c - 8} ${wy - 13}A15 15 0 0 0 ${c - 8} ${wy + 13}M${c + 8} ${wy - 13}A15 15 0 0 1 ${c + 8} ${wy + 13}`}
        />
        {glyph && (
          <g className={`coilgl${lit ? ' on' : ''}`} transform={`translate(${c - 6.5} ${wy - 6.5}) scale(.8125)`}>
            {glyphPaths(glyph)}
          </g>
        )}
        <text className={`coiltxt${lit ? ' on' : ''}`} x={c} y={wy - 22} textAnchor="middle">
          {tag}
        </text>
        <text className={`coiltxt${lit ? ' on' : ''}`} x={c} y={wy + 32} textAnchor="middle">
          {lit || !scene.traced ? 'MATCH' : 'NO MATCH'}
        </text>
      </g>
      <line className="rail" x1={width - 4} y1={2} x2={width - 4} y2={height - 2} />
    </svg>
  );
}

/** A measured box holding one circuit drawing: the circuits sheet and the composer. */
export function Circuit({
  condition,
  trace = null,
  tag,
  glyph,
  describer,
  label,
  mode = null,
  selected = null,
  onPick,
  className = '',
  children,
}: {
  condition: ConditionNode;
  trace?: Record<number, GateTrace> | null;
  tag: string;
  glyph: Glyph | null;
  describer: Describer;
  label: string;
  mode?: SvgProps['mode'];
  selected?: Pick | null;
  onPick?: (pick: Pick) => void;
  className?: string;
  children?: ReactNode;
}) {
  const [ref, width] = useBoxWidth<HTMLDivElement>();
  const scene = useScene(condition, trace, width, describer);
  return (
    <div className={`rungbox ${className}`} ref={ref}>
      {scene && (
        <CircuitSvg
          scene={scene}
          tag={tag}
          glyph={glyph}
          mode={mode}
          selected={selected}
          onPick={onPick}
          label={label}
          boxWidth={width}
        />
      )}
      {children}
    </div>
  );
}
