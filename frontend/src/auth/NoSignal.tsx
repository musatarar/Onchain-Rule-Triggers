import { useEffect, useRef } from 'react';
import { LOCK_MS, prefersReducedMotion } from './copy.ts';

export type Signal = 'none' | 'tuning' | 'fault' | 'locking' | 'locked';

const W = 192;
const H = 108;
const BAR = 26;

/**
 * The off-air picture behind the signed-out panels: low-resolution phosphor
 * noise with a slow hold bar rolling through it. Drawn small and scaled up
 * (pixelated), so it costs a 192×108 buffer at 12 fps. It sharpens and rolls
 * faster while tuning, flares on a fault, and fades out as the signal locks.
 * Under reduced motion it draws one still frame per state.
 */
export function NoSignal({ signal }: { signal: Signal }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const current = useRef(signal);
  current.current = signal;

  useEffect(() => {
    const el = canvas.current;
    const ctx = el?.getContext('2d');
    if (!el || !ctx) return;
    el.width = W;
    el.height = H;
    const image = ctx.createImageData(W, H);
    const px = image.data;
    const still = prefersReducedMotion();
    let raf = 0;
    let last = 0;
    let bar = H * 0.3;
    let lockStart = 0;

    const frame = (t: number) => {
      const s = current.current;
      const fps = s === 'locking' ? 30 : 12;
      if (!still && t - last < 1000 / fps) {
        raf = requestAnimationFrame(frame);
        return;
      }
      last = t;
      if (s === 'locking') lockStart ||= t;
      else lockStart = 0;
      const fade = s === 'locked' ? 0 : s === 'locking' ? Math.max(0, 1 - (t - lockStart) / LOCK_MS) : 1;
      const gain = s === 'fault' ? 1 : s === 'tuning' ? 0.8 : 0.6;
      bar = (bar + (s === 'locking' ? 11 : s === 'tuning' ? 2.4 : 0.7)) % (H + BAR);
      for (let y = 0; y < H; y++) {
        const inBar = y > bar - BAR && y < bar ? 1.7 : 1;
        const tear = s === 'locking' && Math.random() < 0.08 ? 1.6 : 1;
        for (let x = 0; x < W; x++) {
          const v = Math.random();
          const g = Math.min(255, v * v * 255 * gain * inBar * tear * fade);
          const i = (y * W + x) * 4;
          px[i] = g * 0.36;
          px[i + 1] = g;
          px[i + 2] = g * 0.56;
          px[i + 3] = 255;
        }
      }
      ctx.putImageData(image, 0, 0);
      if (!still && !document.hidden && s !== 'locked') raf = requestAnimationFrame(frame);
    };

    const resume = () => {
      if (!document.hidden && !still) {
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(frame);
      }
    };
    raf = requestAnimationFrame(frame);
    document.addEventListener('visibilitychange', resume);
    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener('visibilitychange', resume);
    };
    // A still frame is redrawn when the state changes; a live one reads the ref.
  }, [prefersReducedMotion() ? signal : null]);

  return <canvas ref={canvas} className="nosig" aria-hidden="true" />;
}
