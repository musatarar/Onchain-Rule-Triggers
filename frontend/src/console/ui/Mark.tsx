/**
 * The Phosphor mark: an energised coil (two facing arcs around a lit disc) on
 * its lead and tail wires, drawn like the coil in the circuit diagram. In
 * currentColor; the glow comes from the tube's stylesheet, not from here.
 */
export function Mark({ size = 28 }: { size?: number }) {
  return (
    <svg className="mark" width={size} height={size} viewBox="0 0 48 48" aria-hidden="true" focusable="false">
      <path d="M2 24H9M39 24H46" />
      <path d="M18 11A14 14 0 0 0 18 37M30 11A14 14 0 0 1 30 37" />
      <circle cx="24" cy="24" r="5" />
    </svg>
  );
}
