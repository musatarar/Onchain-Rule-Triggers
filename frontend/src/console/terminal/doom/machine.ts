/**
 * DOOM as a machine the page drives: public/doom/doom.wasm (built by
 * frontend/doom/build.sh) instantiated over the WASI shim, with the
 * "phosphor" imports its platform layer calls (doomgeneric_phosphor.c).
 * Nothing here touches the DOM, so the tests boot the real engine in Node.
 */
import { type Disk, Wasi, WasiExit } from './wasi.ts';

export const SCREEN_WIDTH = 320;
export const SCREEN_HEIGHT = 200;
/** Game tics a second; the engine runs one step a tic. */
export const TICRATE = 35;

/** A sound effect: mono samples in [-1, 1] at `rate` Hz. */
export type Sample = { rate: number; data: Float32Array<ArrayBuffer> };

/** Where sound effects play. `vol` is 0–127 and `sep` 0 (left) to 254 (right). */
export interface Speaker {
  /** `sample` decodes the lump; call it only when `lump` isn't cached, and only during this call. */
  start(channel: number, lump: number, sample: () => Sample | null, vol: number, sep: number): void;
  update(channel: number, vol: number, sep: number): void;
  stop(channel: number): void;
  playing(channel: number): boolean;
}

export interface DoomHost {
  /**
   * Milliseconds on the game clock. The engine waits for a tic by reading it
   * in a loop, so it has to move during a call: the startup waits for one.
   */
  now(): number;
  /**
   * A finished frame: 320×200 palette indices, and the palette (256 entries
   * of b, g, r, unused) when it changed since the last frame. Both are views
   * into the machine's memory, valid only during the call.
   */
  frame(pixels: Uint8Array, palette: Uint8Array | null): void;
  stdout(text: string): void;
  stderr(text: string): void;
  speaker?: Speaker;
}

type Exports = {
  memory: WebAssembly.Memory;
  _initialize(): void;
  malloc(size: number): number;
  doom_start(argc: number, argv: number): void;
  doom_tick(): void;
  doom_mouse(buttons: number, dx: number, dy: number): void;
};

/**
 * A DMX sound lump: format 3, the sample rate, the sample count, then 8-bit
 * unsigned samples padded by 16 bytes at each end, which DMX never played.
 * Too short or malformed lumps are silent, as they were in DMX.
 */
export function readDmx(lump: Uint8Array): Sample | null {
  if (lump.length < 8 || lump[0] !== 3 || lump[1] !== 0) return null;
  const header = new DataView(lump.buffer, lump.byteOffset, 8);
  const rate = header.getUint16(2, true);
  const length = header.getUint32(4, true);
  if (length > lump.length - 8 || length <= 48 || rate === 0) return null;
  const bytes = lump.subarray(8 + 16, 8 + length - 16);
  const data = new Float32Array(bytes.length);
  for (let i = 0; i < bytes.length; i++) data[i] = (bytes[i] - 128) / 128;
  return { rate, data };
}

/**
 * Pointer-lock motion arrives in CSS pixels. It is doubled so a sweep of the
 * mouse turns a useful distance at the game's default sensitivity, then gets
 * Chocolate Doom's acceleration: motion past 10 in a tic counts double.
 */
function turn(pixels: number): number {
  const size = Math.abs(Math.round(pixels * 2));
  return Math.sign(pixels) * (size > 10 ? (size - 10) * 2 + 10 : size);
}

export class Doom {
  /** The exit status once the game has quit or failed; null while it runs. */
  status: number | null = null;
  private readonly exports: Exports;
  private readonly host: DoomHost;
  private readonly keys: number[] = [];
  private buttons = 0;
  private motion = 0;
  private mouseDirty = false;
  /** The engine's clock starts at its first reading (i_timer.c); null until then. */
  private base: number | null = null;
  private ran = -1;

  private constructor(exports: Exports, host: DoomHost) {
    this.exports = exports;
    this.host = host;
  }

  /** The engine's I_GetTime(): whole tics since its first reading. */
  private get tic(): number {
    return this.base === null ? 0 : Math.floor(((Math.floor(this.host.now()) - this.base) * TICRATE) / 1000);
  }

  /**
   * Whether a tic the engine hasn't run is due. Ticking only then keeps the
   * engine out of its wait-for-a-tic loop, which would spin the page.
   */
  get due(): boolean {
    return this.status === null && this.tic > this.ran;
  }

  /**
   * Instantiates a fresh machine over `disk` (which must hold the IWAD) and
   * runs the engine's startup with `args`, as on a command line.
   */
  static async boot(module: WebAssembly.Module, disk: Disk, host: DoomHost, args: string[]): Promise<Doom> {
    const wasi = new Wasi({ disk, stdout: host.stdout, stderr: host.stderr });
    let doom: Doom | null = null;
    const memory = () => new Uint8Array(wasi.memory!.buffer);
    const phosphor = {
      frame: (pixels: number, palette: number, changed: number) =>
        host.frame(memory().subarray(pixels, pixels + SCREEN_WIDTH * SCREEN_HEIGHT), changed ? memory().subarray(palette, palette + 1024) : null),
      ticks_ms: () => {
        const now = Math.floor(host.now());
        if (doom && doom.base === null) doom.base = now;
        return now >>> 0;
      },
      poll_key: () => doom?.keys.shift() ?? -1,
      title: () => {},
      sfx_start: (channel: number, lump: number, data: number, length: number, vol: number, sep: number) => {
        host.speaker?.start(channel, lump, () => readDmx(memory().subarray(data, data + length)), vol, sep);
        return channel;
      },
      sfx_update: (channel: number, vol: number, sep: number) => host.speaker?.update(channel, vol, sep),
      sfx_stop: (channel: number) => host.speaker?.stop(channel),
      sfx_playing: (channel: number) => (host.speaker?.playing(channel) ? 1 : 0),
    };
    const instance = await WebAssembly.instantiate(module, { wasi_snapshot_preview1: wasi.imports, phosphor });
    const exports = instance.exports as unknown as Exports;
    wasi.memory = exports.memory;
    doom = new Doom(exports, host);
    doom.run(() => {
      exports._initialize();
      exports.doom_start(args.length, doom!.argv(args));
    });
    return doom;
  }

  /** Runs the engine's next step: every tic that's due on the host's clock. */
  tick(): void {
    this.ran = this.tic;
    this.run(() => {
      if (this.mouseDirty) {
        this.exports.doom_mouse(this.buttons, turn(this.motion), 0);
        this.motion = 0;
        this.mouseDirty = false;
      }
      this.exports.doom_tick();
    });
  }

  /** Queues a key: `key` is what the game binds (doomkeys.h), `typed` the character it types. */
  key(down: boolean, key: number, typed: number): void {
    if (this.status === null) this.keys.push((down ? 1 << 16 : 0) | ((typed & 0xff) << 8) | (key & 0xff));
  }

  /** Mouse buttons held (bit 0 left, 1 right, 2 middle) and sideways motion, summed until the next tic. */
  mouse(buttons: number, dx: number): void {
    this.buttons = buttons;
    this.motion += dx;
    this.mouseDirty = true;
  }

  private argv(args: string[]): number {
    const encoder = new TextEncoder();
    const argv = this.exports.malloc(args.length * 4);
    args.forEach((arg, i) => {
      const bytes = encoder.encode(`${arg}\0`);
      const ptr = this.exports.malloc(bytes.length);
      new Uint8Array(this.exports.memory.buffer).set(bytes, ptr);
      new DataView(this.exports.memory.buffer).setUint32(argv + i * 4, ptr, true);
    });
    return argv;
  }

  private run(step: () => void): void {
    if (this.status !== null) return;
    try {
      step();
    } catch (error) {
      if (!(error instanceof WasiExit)) {
        this.status = -1;
        throw error;
      }
      this.status = error.code;
    }
  }
}

