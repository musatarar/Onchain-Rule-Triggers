/**
 * How a DOOM frame lands on the tube. The game draws 320×200 palette
 * indices; a lookup of 256 RGBA words turns them into canvas pixels. On
 * PHOSPHOR every palette colour becomes the step of the phosphor ramp at its
 * brightness, so the game obeys the One Hue Rule like the rest of the glass;
 * COLOUR keeps the game's own palette.
 *
 * Brightness is half luma, half the strongest channel: luma alone (what a
 * monochrome VGA monitor showed) sinks DOOM's red menu text into the red
 * title art behind it.
 */

export type Picture = 'phosphor' | 'colour';

// DESIGN.md's ramp, tube black to hot phosphor.
const RAMP = ['#03120a', '#0a2814', '#103f20', '#1c6e37', '#2aa452', '#3fd873', '#5dff8f', '#9dffbd'].map((hex) => [
  parseInt(hex.slice(1, 3), 16),
  parseInt(hex.slice(3, 5), 16),
  parseInt(hex.slice(5, 7), 16),
]);

/** A canvas pixel as a little-endian RGBA word, the order ImageData's bytes take. */
function word(r: number, g: number, b: number): number {
  return (0xff000000 | (b << 16) | (g << 8) | r) >>> 0;
}

/**
 * The ramp colour for each brightness 0–255. Brightness is lifted a little
 * (gamma 0.8) because the dim end of the ramp is nearly black and DOOM's
 * corridors are dark already.
 */
const PHOSPHOR = Uint32Array.from({ length: 256 }, (_, luma) => {
  const at = Math.pow(luma / 255, 0.8) * (RAMP.length - 1);
  const low = Math.min(Math.floor(at), RAMP.length - 2);
  const mix = at - low;
  const [r, g, b] = [0, 1, 2].map((c) => Math.round(RAMP[low][c] + (RAMP[low + 1][c] - RAMP[low][c]) * mix));
  return word(r, g, b);
});

/** Canvas words for a DOOM palette: 256 entries of b, g, r and an unused byte. */
export function paletteWords(palette: Uint8Array, picture: Picture): Uint32Array {
  const words = new Uint32Array(256);
  for (let i = 0; i < 256; i++) {
    const b = palette[i * 4];
    const g = palette[i * 4 + 1];
    const r = palette[i * 4 + 2];
    words[i] = picture === 'colour' ? word(r, g, b) : PHOSPHOR[Math.round((0.299 * r + 0.587 * g + 0.114 * b + Math.max(r, g, b)) / 2)];
  }
  return words;
}

/** Writes a frame of palette indices into `out`, a Uint32 view of the canvas ImageData. */
export function paint(pixels: Uint8Array, words: Uint32Array, out: Uint32Array): void {
  for (let i = 0; i < pixels.length; i++) out[i] = words[pixels[i]];
}
